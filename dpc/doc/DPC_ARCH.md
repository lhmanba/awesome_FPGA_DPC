# DPC 架构详细设计

| 属性 | 值 |
|------|-----|
| 版本 | 1.0.0 |
| 日期 | 2026-05-15 |
| 状态 | Phase 1.3 产出 |

---

## 一、顶层架构

### 1.1 模块框图

```
                        dpc_top
   ┌──────────────────────────────────────────────────────────┐
   │                                                          │
   │  ┌─────────────┐                                         │
   │  │  dpc_reg    │◄──── cfg_addr/cfg_wdata/cfg_wen/cfg_ren │
   │  │  寄存器接口   │─────► cfg_rdata                          │
   │  │             │                                         │
   │  │ IMG_WIDTH───┤────► 各模块                                │
   │  │ IMG_HEIGHT──┤                                         │
   │  │ IMG_FORMAT──┤                                         │
   │  │ BYPASS──────┤────► dpc_correction.mux                  │
   │  │ ENABLE──────┤────► dpc_bad_table / dpc_correction      │
   │  │ BAD_TABLE_*─┤────► dpc_bad_table                       │
   │  └─────────────┘                                         │
   │                                                          │
   │  s_axis_tvalid ─┬─────────────────────────────────────┐  │
   │  s_axis_tdata ──┤                                     │  │
   │  s_axis_tuser ──┤                                     │  │
   │  s_axis_tlast ──┤                                     │  │
   │                 │                                     │  │
   │                 ▼                                     │  │
   │  ┌──────────────────────┐                              │  │
   │  │  dpc_coord_tracker   │                              │  │
   │  │                      │                              │  │
   │  │  pixel_x[11:0] ──────┤──► dpc_bad_table             │  │
   │  │  pixel_y[11:0] ──────┤──► dpc_bad_table             │  │
   │  │  s_tdata_d1 ─────────┤──► dpc_linebuf               │  │
   │  │  s_tvalid_d1 ────────┤──► dpc_linebuf               │  │
   │  └──────────────────────┘                              │  │
   │                                                          │
   │                 ┌────────────┐                            │
   │  pixel_x ──────►│ dpc_bad    │                            │
   │  pixel_y ──────►│ _table     │──► is_bad ──► correction   │
   │                 └────────────┘                            │
   │                                                          │
   │                 ┌────────────┐                            │
   │  s_tdata_d1 ──►│ dpc_linebuf│──► neighbors ─► correction  │
   │                 └────────────┘                            │
   │                                                          │
   │                 ┌─────────────────┐                       │
   │  is_bad ──────►│                 │                       │
   │  neighbors ──►│ dpc_correction   │──► corrected_pixel     │
   │  s_tdata ────►│                 │──► m_axis_tdata         │
   │                 └─────────────────┘                       │
   │                                                          │
   │  m_axis_tvalid = s_axis_tvalid (经过延迟对齐)              │
   │  m_axis_tuser  = s_axis_tuser  (经过延迟对齐)              │
   │  m_axis_tlast  = s_axis_tlast  (经过延迟对齐)              │
   │  s_axis_tready = ~fifo_full (或恒为1, 若下游不反压)        │
   │                                                          │
   └──────────────────────────────────────────────────────────┘
```

### 1.2 流水线延迟分配

```
总延迟 = 5 拍 (clock cycles)

Cycle 0: 输入采样
  s_axis_tvalid & s_axis_tready → 锁存 tdata, tuser, tlast

Cycle 1: 坐标计算
  pixel_x, pixel_y 更新
  → 输出: (x, y, tdata_d1)

Cycle 2: 坏点表查询 (二分查找流水线内部进行)
  → 输入: (x, y)
  → 已流水化，不影响吞吐

Cycle 3: 邻域组装
  LineBuf 读出上行同色像素
  Current reg 提供左邻域像素
  → 输出: {up_pixel, left_pixel, up_left_pixel}

Cycle 4: 校正计算
  if (is_bad):
    corrected = avg(up_pixel, left_pixel, ...)
  else:
    corrected = original
  → 输出: corrected_pixel

Cycle 5: 输出驱动
  m_axis_tvalid = s_axis_tvalid_d5
  m_axis_tdata  = corrected_pixel
  m_axis_tuser  = s_axis_tuser_d5
  m_axis_tlast  = s_axis_tlast_d5

总延迟: 5 拍 ✓ (< 16 拍约束)
```

---

## 二、子模块详细设计

### 2.1 dpc_coord_tracker — 坐标跟踪器

**功能**: 为每个输入像素生成行列坐标 (pixel_x, pixel_y)

**状态机**: 无（纯计数器逻辑）

```
信号说明:
  - pixel_x[11:0]: 列坐标, 范围 0 ~ IMG_WIDTH-1
  - pixel_y[11:0]: 行坐标, 范围 0 ~ IMG_HEIGHT-1

计数规则:
  SOF (tuser=1)       → pixel_x=0, pixel_y=0
  EOL (tlast=1)       → pixel_x=0, pixel_y=pixel_y+1
  其他 valid & ready   → pixel_x=pixel_x+1

输出:
  - 1拍延迟的 tdata, tvalid 给 LineBuf
  - pixel_x, pixel_y 给 dpc_bad_table
```

**资源估算**:
- 2个12bit计数器 → 24 FF
- 比较逻辑 (tuser/tlast检测) → 约15 LUT
- tdata延迟寄存器: 12bit → 12 FF
- **合计: ~36 FF, ~15 LUT**

### 2.2 dpc_bad_table — 坏点表

**功能**: 存储坏点坐标表，查找当前像素是否为坏点

**存储方案**: 分布式RAM (LUT-RAM)，深度1024，宽度24bit

```
24bit 复合键: {pixel_y[11:0], pixel_x[11:0]}

二分查找流水线:
  Cycle 0: mid_idx = 512, rd_addr = 512
  Cycle 1: read_data_512 有效, cmp0 = (key >= data_512)
           mid_idx = cmp0 ? 768 : 256
  Cycle 2: read_data_768 有效, cmp1 = (key >= data_768)
           mid_idx = ...
  ...
  Cycle 10: 最终比较, is_bad = (table[mid_idx] == key)

10级流水线，每级处理不同像素:
  像素P0进入Cycle0时，像素P0~P9各占一级
  第1个结果在10拍后出现
  之后每个时钟输出1个结果
```

**坏点表写入接口**:
```
来自 dpc_reg:
  bad_wr_en      → 写使能
  bad_wr_addr[9:0] → 写地址 (0~1023)
  bad_wr_data[23:0] → {Y[11:0], X[11:0]}
  bad_table_lock  → 锁定后禁止写入

写入流程:
  1. 复位后 bad_table_lock=0, count=0
  2. 上位机依次写入坏点坐标 (需预排序)
  3. 上位机设置 bad_table_lock=1
  4. FPGA开始二分查找
```

**资源估算**:
- 分布式RAM 1024×24bit = 24576 bits
  - 每LUT可构成32×1bit RAM
  - 需要 24576/32 = 768 个LUT (作为RAM)
  - 地址译码 ≈ 80 LUT
- 流水线比较器: 10级 × 24bit比较器 ≈ 60 LUT
- 流水线寄存器: 10级 × (mid_idx + cmp) ≈ 140 FF
- **合计: ~850 LUT, ~140 FF, 0 BRAM**

### 2.3 dpc_linebuf — 行缓冲

**功能**: 缓存图像行的像素值，提供上行邻域

**方案**: 单行缓冲 (1-Line Buffer)

```
Buffer深度: MAX_WIDTH × (DATA_WIDTH bits)
          = 3840 × 10 = 38400 bits

实现方式: 简单移位寄存器链 (Shift Register)
  - 3840级 × 10bit 移位寄存器
  - 每个时钟: 新像素进入 [0], 最旧像素从 [3839] 移出

读端口:
  - 当前位置 [0]: 当前像素 (直接使用, 不需LineBuf)
  - 2拍前位置 [2]: 左邻域同色像素 (x-2)
  - 同行位置 [x]: 当前行对应列 (无延迟, 就是输入)
  
  上行的同色邻域:
  - 上行第x列: 需要额外一行缓冲
  - 如果只用1行缓冲 → 只能提供上行x位置的像素
  - 同色判断: Bayer RGGB下, 上行同色 = 上两行同列

实际方案: 使用第[0] ~ 第[3]的移位寄存器tap
  - tap[0]: 当前像素
  - tap[1]: 左1像素 (x-1)
  - tap[2]: 左2像素 (x-2) ← 同色左邻域
  - tap[3]: 左3像素 (x-3)
  
这不需要真正的行缓冲! 只需要4级移位寄存器!
```

**简化方案**（MVP，推荐）:
```
不对，让我重新想。同色邻域间距为2。
对于 x 位置的 R 像素：
  - 同色左邻域: x-2 位置的 R 像素 (当前行, 已过去2拍)
  - 同色上邻域: x 位置的上上行 R 像素 (需要缓存一整行)

需要: 1行缓冲 (深度=MAX_WIDTH)
  - LineBuf[0]: 上一行第0列的像素
  - LineBuf[1]: 上一行第1列的像素
  - ...
  - LineBuf[x]: 上一行第x列的像素

但是: Bayer RGGB下, 第y行的像素与第y-1行不同色!
  - y=0 (RG行) 与 y=1 (GB行): 同列不同色
  - y=0 (RG行) 与 y=2 (RG行): 同列同色

所以"上行同色"指的是 y-2, 不是 y-1!

需要: 2行缓冲 (深度=MAX_WIDTH×2)
  或者: 1行缓冲 + 知道Bayer模式来推算
  
更简单的思路: 缓冲2行
  - LineBuf[0][x]: y-1 行的像素
  - LineBuf[1][x]: y-2 行的像素

y行的像素R: 同色上邻域在 y-2 行, 所以读 LineBuf[1][x]
```

**最终方案**:
```
2行缓冲:
  LineBuf[0]: 3840 × 10bit → 1行 (y-1)
  LineBuf[1]: 3840 × 10bit → 1行 (y-2)

读逻辑:
  current_row_pixel = s_axis_tdata (直接输入)
  up1_row_pixel     = LineBuf[0][pixel_x]  (y-1行, 可能不同色)
  up2_row_pixel     = LineBuf[1][pixel_x]  (y-2行, 同色!)
  left_pixel        = shift_reg[2]          (x-2, 同行同色)

校正时:
  if (is_bad):
    同色邻域 = [left_pixel, up2_row_pixel]
    if (up2_row_pixel 存在 && left_pixel 存在):
      corrected = avg(up2_row_pixel, left_pixel)
    elif (left_pixel 存在):
      corrected = left_pixel
    elif (up2_row_pixel 存在):
      corrected = up2_row_pixel
    else:
      corrected = current_pixel (无法校正)
```

**资源估算**:
```
方案A: 移位寄存器实现行缓冲
  SRL16E (Xilinx) / SRL (Efinity)
  2行 × 3840 × 10bit = 76800 bits
  使用 LUT-RAM: 76800/32 ≈ 2400 LUT ← 太多!

方案B: 只用移位寄存器tap (当前行左右邻域)
  4级 × 10bit = 40 FF
  不需要行缓冲
  ← 仅用同行邻域, 无上行数据

MVP选择: 方案B (无行缓冲)
  仅使用同行左/右邻域
  右邻域需要"预读" → 当前流模式不可行
  所以MVP只用左邻域 + 输入打拍

优化方案: 增加1个BRAM做1行缓冲
  1行 × 3840 × 10bit = 38.4 Kbits
  TJ180 BRAM: 每块10Kbit → 需 4 块
  ← 与"0 BRAM"约束冲突, 但功能完整

建议: MVP用方案B(无缓冲,仅同行左邻域), 
       优化版用方案A(1个BRAM,1行缓冲)
```

**MVP资源 (方案B)**:
- 4级移位寄存器: 4×10bit = 40 FF
- **合计: ~40 FF, 0 LUT, 0 BRAM**

**优化版资源 (方案A)**:
- 1×BRAM 38.4Kb → 4 BRAM块
- **合计: ~20 FF, ~30 LUT, 4 BRAM**

### 2.4 dpc_correction — 校正算法

**功能**: 收到 is_bad 信号和邻域像素后，计算校正值

**算法**:
```
输入: original_pixel, is_bad, left_pixel, up_pixel
输出: corrected_pixel

if (BYPASS):
    corrected = original_pixel
elif (is_bad):
    if (left_valid && up_valid):
        corrected = (left_pixel + up_pixel) >> 1
    elif (left_valid):
        corrected = left_pixel
    elif (up_valid):
        corrected = up_pixel
    else:
        corrected = original_pixel  // 无法校正
else:
    corrected = original_pixel
```

**边界有效性判断**:
```
left_valid  = (pixel_x >= 2)                          // 存在左邻域
up_valid    = (pixel_y >= 2) && line_buf_valid          // 存在上行邻域

Bayer同色检查:
  left同色: pixel_y相同 → 同色(因为在同一行, 间距2意味着同Bayer相位)
  up同色:   pixel_y差2 → 同色(因为Bayer每2行重复)
```

**资源估算**:
- 加法器: 10bit + 10bit → 11bit → 约15 LUT
- 移位: 免费 (>>1)
- MUX选择器: 约10 LUT
- **合计: ~25 LUT, ~5 FF**

### 2.5 dpc_reg — 寄存器接口

**功能**: awesom 标准寄存器接口

见 DPC_坏点校正_设计思路与计划.md 第四章寄存器定义。

**资源估算**:
- 地址译码: 6bit地址 → 约15 LUT
- 读数据MUX: 约30 LUT
- 寄存器存储: 约15个32bit寄存器 → 480 FF
- **合计: ~45 LUT, ~480 FF**

---

## 三、顶层连线汇总

```
dpc_top 例化:

dpc_coord_tracker (
    .aclk, .aresetn,
    .s_axis_tvalid, .s_axis_tready,
    .s_axis_tdata, .s_axis_tuser, .s_axis_tlast,
    .pixel_x, .pixel_y,
    .s_tdata_d1, .s_tvalid_d1
);

dpc_bad_table (
    .aclk, .aresetn,
    .pixel_x, .pixel_y,
    .is_bad,
    .bad_wr_en, .bad_wr_addr, .bad_wr_data, .bad_table_lock
);

dpc_linebuf (
    .aclk, .aresetn,
    .pixel_x, .pixel_y,
    .s_tdata_d1, .s_tvalid_d1,
    .left_pixel, .up_pixel,
    .left_valid, .up_valid
);

dpc_correction (
    .aclk, .aresetn,
    .original_pixel, .is_bad,
    .left_pixel, .up_pixel,
    .left_valid, .up_valid,
    .bypass,
    .corrected_pixel
);

dpc_reg (
    .aclk, .aresetn,
    .cfg_addr, .cfg_wdata, .cfg_wen, .cfg_ren, .cfg_rdata,
    .img_width, .img_height, .img_format,
    .bypass, .enable,
    .bad_wr_en, .bad_wr_addr, .bad_wr_data, .bad_table_lock,
    .bad_hit_count, .frame_count
);
```

---

## 四、资源预估汇总

| 模块 | LUT | FF | BRAM | DSP |
|------|-----|-----|------|-----|
| dpc_coord_tracker | 15 | 36 | 0 | 0 |
| dpc_bad_table | 850 | 140 | 0 | 0 |
| dpc_linebuf (MVP) | 0 | 40 | 0 | 0 |
| dpc_correction | 25 | 5 | 0 | 0 |
| dpc_reg | 45 | 480 | 0 | 0 |
| 顶层对齐/粘合 | 30 | 60 | 0 | 0 |
| **MVP 合计** | **~965** | **~761** | **0** | **0** |

TJ180 总资源参考:
- LUT: ~100,000 (965 / 100,000 ≈ 0.97%) ✓
- FF:  ~200,000 (761 / 200,000 ≈ 0.38%) ✓
- BRAM: 1,280块 (0/1280 = 0%) ✓
- DSP: ~240 (0/240 = 0%) ✓

全部在约束范围内! ✓

# DPC Vibe-Coding 提示词集

> 用于 Phase 2 RTL 代码开发时喂给 AI 的结构化提示词。
> 按模块拆分，每个模块独立一个 prompt。

---

## 提示词 1: dpc_coord_tracker — 坐标跟踪器

```
# 角色
你是一位专业的FPGA视频处理工程师，精通Verilog硬件设计和AXI-Stream视频协议。

# 任务
实现 dpc_coord_tracker 模块，功能为实时跟踪视频流中每个像素的 (X, Y) 坐标。

# 输入输出规格
- 输入: AXI-Stream 视频流 (s_axis_tvalid, s_axis_tdata, s_axis_tuser, s_axis_tlast)
- 输出: pixel_x[11:0], pixel_y[11:0]
- 附加输出: s_tdata_d1[9:0] (1拍延迟的像素数据)

# 功能要求
1. 收到 SOF (s_axis_tuser=1) 时，坐标复位为 (0, 0)
2. 收到 EOL (s_axis_tlast=1) 时，列归零，行+1
3. 每个 valid&ready 握手成功的时钟，列+1
4. 支持BYPASS模式 (坐标仍然运行)
5. 坐标范围: 0 ~ 3840 (MAX_WIDTH), 0 ~ 2160 (MAX_HEIGHT)，用参数化控制

# 技术约束
- 语言: Verilog
- 时钟频率: 125 MHz
- 延迟: 1拍 (输出比输入晚1个时钟)
- BRAM: 0块
- 复位: 异步低有效 aresetn

# 接口定义
```verilog
module dpc_coord_tracker #(
    parameter C_MAX_WIDTH  = 3840,
    parameter C_MAX_HEIGHT = 2160,
    parameter C_DATA_WIDTH = 10
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire        s_axis_tvalid,
    input  wire        s_axis_tready,
    input  wire [C_DATA_WIDTH-1:0] s_axis_tdata,
    input  wire        s_axis_tuser,
    input  wire        s_axis_tlast,
    output reg  [11:0] pixel_x,
    output reg  [11:0] pixel_y,
    output reg  [C_DATA_WIDTH-1:0] s_tdata_d1,
    output reg         s_tvalid_d1
);
```

# 输出要求
1. 完整的模块Verilog代码
2. 关键逻辑处的注释说明
3. 注意没有输入数据时坐标保持不变
```

---

## 提示词 2: dpc_bad_table — 坏点表存储与查询

```
# 角色
你是一位专业的FPGA视频处理工程师，精通Verilog硬件设计和查找表优化。

# 任务
实现 dpc_bad_table 模块，功能为存储1024个坏点坐标并执行流水线查找。

# 功能要求

## 坏点表存储
- 容量: 1024 个条目
- 每条目: 24bit = {Y[11:0], X[11:0]}
- 存储介质: 分布式RAM (LUT-RAM)，不能使用BRAM
- 写入接口:
  - bad_wr_en: 写使能
  - bad_wr_addr[9:0]: 写入地址 (0~1023)
  - bad_wr_data[23:0]: 坏点坐标
- 要求上位机按 {Y, X} 升序写入坏点表 (不需要硬件排序)
- bad_table_lock 拉高后禁止写入，开始查找模式

## 查找功能
- 输入: pixel_x[11:0], pixel_y[11:0]
- 输出: is_bad (当前像素是否为坏点)
- 复合键: {pixel_y, pixel_x} 共24bit
- 查找方式: 流水线二分查找
  - 共 log2(1024) = 10 级流水线
  - 每级处理不同像素 (不影响吞吐)
  - 总延迟: 10个时钟周期
  - 吞吐: 1像素/时钟

## 二分查找流水线设计
- 使用 generate-for 循环生成10级流水线
- 每级: mid_idx_o = cmp_i ? mid_hi : mid_lo
- 最终级: hit = (search_key == table[mid])
- pipelined 输出 is_bad 需要与主数据通路对齐

# 技术约束
- 语言: Verilog
- 时钟频率: 125 MHz
- 延迟: 10拍
- BRAM: 0块 (用分布式RAM)
- 复位: 异步低有效

# 接口定义
```verilog
module dpc_bad_table #(
    parameter C_TABLE_SIZE  = 1024,
    parameter C_ADDR_WIDTH  = 10,
    parameter C_COORD_WIDTH = 12,
    parameter C_KEY_WIDTH   = 24
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [C_COORD_WIDTH-1:0] pixel_x,
    input  wire [C_COORD_WIDTH-1:0] pixel_y,
    output wire        is_bad,
    input  wire        bad_wr_en,
    input  wire [C_ADDR_WIDTH-1:0] bad_wr_addr,
    input  wire [C_KEY_WIDTH-1:0]  bad_wr_data,
    input  wire        bad_table_lock
);
```

# 注意事项
1. DP-RAM 需要双端口: 端口A用于写入，端口B用于二分查找读取
2. 二分查找的10级流水线之间要有明确的寄存器分割
3. 使用 generate 而非重复代码来实现流水线
4. 查找同时不能写入 (bad_table_lock=1时忽略写入)
5. 空表 (count=0) 或查找过程中 is_bad 始终为0

# 输出要求
1. 完整的模块Verilog代码
2. 二分查找流水线的 generate 实现
3. DP-RAM 的例化方式
```

---

## 提示词 3: dpc_linebuf — 行缓冲

```
# 角色
你是一位专业的FPGA视频处理工程师，精通Verilog硬件设计和流式数据处理。

# 任务
实现 dpc_linebuf 模块，功能为缓存图像行数据，提供邻域像素用于坏点校正。

# 功能要求

## MVP方案 (无真实行缓冲)
仅使用当前行的移位寄存器来提供左邻域像素。

- 4级移位寄存器链: 每级存储1个像素 (10bit)
- tap[0] = 当前输入像素 (第1拍后)
- tap[2] = 左邻域同色像素 (x-2位置, 因为Bayer同色间距为2)
- 输出: left_pixel, left_valid
- 上行邻域: 不使用 (MVP无行缓冲)
- up_pixel = 10'd0, up_valid = 1'b0 (暂时置零)

## 优化方案 (真实行缓冲)
如果后续需要加行缓冲:
- 1个BRAM实现的Simple Dual Port RAM
- 深度=3840, 宽度=10bit
- 写端口: 写入当前行像素
- 读端口: 读取上行对应列像素

# 技术约束
- 语言: Verilog
- 时钟频率: 125 MHz
- 延迟: 0拍 (组合逻辑输出 tap 值)
- BRAM: MVP方案0块
- 复位: 异步低有效

# 接口定义 (MVP)
```verilog
module dpc_linebuf #(
    parameter C_MAX_WIDTH  = 3840,
    parameter C_DATA_WIDTH = 10
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [11:0] pixel_x,
    input  wire [11:0] pixel_y,
    input  wire [C_DATA_WIDTH-1:0] s_tdata,
    input  wire        s_tvalid,
    output wire [C_DATA_WIDTH-1:0] left_pixel,
    output wire [C_DATA_WIDTH-1:0] up_pixel,
    output wire        left_valid,
    output wire        up_valid
);
```

# 输出要求
1. 完整的模块Verilog代码 (MVP版 + 注释标注优化版接口)
2. 移位寄存器的实现
3. left_valid/up_valid 的边界判断逻辑
4. 说明: 本模块在MVP方案下仅提供同行左邻域，上行邻域留空给优化版
```

---

## 提示词 4: dpc_correction — 校正算法核心

```
# 角色
你是一位专业的FPGA视频处理工程师，精通Verilog硬件设计和图像处理算法。

# 任务
实现 dpc_correction 模块，功能为根据坏点检测信号和邻域像素计算校正值。

# 功能要求

## 校正算法
- 非坏点且非BYPASS: 原值直通
- BYPASS 模式: 原值直通 (无视坏点信号)
- 坏点 + 左邻域有效 + 上邻域有效: corrected = (left + up) >> 1
- 坏点 + 仅左邻域有效: corrected = left
- 坏点 + 仅上邻域有效: corrected = up
- 坏点 + 无邻域: corrected = original (无法校正, 保持原值)

## 邻域有效性
- left_valid: 由dpd_linebuf给出
- up_valid: 由dpc_linebuf给出 (MVP下恒为0)

# 技术约束

- 语言: Verilog
- 时钟频率: 125 MHz
- 延迟: 0拍 (组合逻辑, 建议加1拍输出寄存器做时序隔离)
- 纯组合逻辑加法+选择器, 无乘法
- BRAM: 0块
- DSP: 0个
- 复位: 异步低有效

# 接口定义
```verilog
module dpc_correction #(
    parameter C_DATA_WIDTH = 10
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [C_DATA_WIDTH-1:0] original_pixel,
    input  wire        is_bad,
    input  wire [C_DATA_WIDTH-1:0] left_pixel,
    input  wire [C_DATA_WIDTH-1:0] up_pixel,
    input  wire        left_valid,
    input  wire        up_valid,
    input  wire        bypass,
    output reg  [C_DATA_WIDTH-1:0] corrected_pixel
);
```

# 输出要求
1. 完整的模块Verilog代码
2. 校正模式选择的状态说明
3. 组合逻辑 + 1拍输出寄存器的实现
```

---

## 提示词 5: dpc_reg — 寄存器接口

```
# 角色
你是一位专业的FPGA视频处理工程师，精通Verilog硬件设计和寄存器接口实现。

# 任务

实现 dpc_reg 模块，功能为实现 awesom 标准寄存器接口，提供 IP 控制和状态寄存器。

# 寄存器地址映射

| 偏移 | 名称 | 类型 | 位域 |
|------|------|------|------|
| 0x00 | IP_VERSION | RO | [31:0] = 32'h0100_0000 |
| 0x04 | IP_CTRL | RW | [0]=enable, [1]=bypass, [2]=debug, [3]=bad_table_lock |
| 0x08 | IP_STATUS | RO | [15:0]=bad_hit_count |
| 0x0C | IP_RESET | WO | [0]=stat_reset (写1清零统计) |
| 0x10 | IMG_WIDTH | RW | [15:0]=图像宽度 |
| 0x14 | IMG_HEIGHT | RW | [15:0]=图像高度 |
| 0x18 | IMG_FORMAT | RW | [1:0]=00:RAW8, 01:RAW10, 10:RAW12 |
| 0x1C | CORR_THRESHOLD | RW | [9:0]=阈值 (MVP暂不使用) |
| 0x20 | BAD_TABLE_ADDR | WO | [9:0]=坏点表写入地址 |
| 0x24 | BAD_TABLE_DATA | WO | [23:0]={Y[11:0], X[11:0]} |
| 0x28 | BAD_TABLE_WR | WO | [0]=写使能, [1]=表锁定 |
| 0x2C | BAD_TABLE_COUNT | RO | [9:0]=当前坏点数量 |
| 0x30 | BAD_HIT_COUNT | RO | [15:0]=累计坏点命中次数 |
| 0x34 | FRAME_COUNT | RO | [31:0]=累计帧数 |

# 技术约束
- 语言: Verilog
- 地址位宽: 8bit (256个地址空间)
- 数据位宽: 32bit
- 读写协议: cfg_wen/cfg_ren 单周期脉冲
- 读数据: cfg_ren 后的下一拍输出 cfg_rdata
- 复位: 异步低有效 (所有RW寄存器复位为默认值)

# 接口定义
```verilog
module dpc_reg #(
    parameter C_ADDR_WIDTH = 8,
    parameter C_DATA_WIDTH = 32
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [C_ADDR_WIDTH-1:0] cfg_addr,
    input  wire [C_DATA_WIDTH-1:0] cfg_wdata,
    input  wire        cfg_wen,
    input  wire        cfg_ren,
    output reg  [C_DATA_WIDTH-1:0] cfg_rdata,
    output wire [15:0] img_width,
    output wire [15:0] img_height,
    output wire [1:0]  img_format,
    output wire        bypass,
    output wire        enable,
    output wire        bad_table_lock,
    output wire [9:0]  bad_wr_addr,
    output wire [23:0] bad_wr_data,
    output wire        bad_wr_en,
    input  wire [15:0] bad_hit_count,
    input  wire [31:0] frame_count,
    input  wire [9:0]  bad_table_count
);
```

# 输出要求
1. 完整的模块Verilog代码
2. 每个寄存器的复位默认值
3. 寄存器访问的时序说明
4. 未使用地址返回0
```

---

## 提示词 6: dpc_top — 顶层集成

```
# 角色
你是一位专业的FPGA视频处理工程师，精通Verilog硬件设计和大规模模块集成。

# 任务
实现 dpc_top 顶层模块，将 dpc_coord_tracker, dpc_bad_table, dpc_linebuf, dpc_correction, dpc_reg 五个子模块集成，实现完整的缺陷像素校正IP。

# 功能要求
1. 例化所有5个子模块
2. 连接AXI-Stream输入输出接口
3. 连接寄存器配置接口
4. 处理各模块间的流水线对齐延迟
5. AXI-Stream控制信号的延迟对齐:
   - tuser: 5拍延迟 (与数据对齐)
   - tlast: 5拍延迟
   - tvalid: 5拍延迟
   - tready: 直接透传或恒为1
6. s_axis_tready 逻辑: 当BYJPASS时恒为1, 否则根据fifo状态

# 关键延迟对齐
各模块输出数据的延迟:
  - coord_tracker → pixel_x/y: 1拍
  - bad_table → is_bad: 10拍
  - linebuf → neighbors: 2拍 (2级移位寄存器)
  - correction → corrected: 1拍

总数据路径延迟: 需要精确对齐!
  is_bad 比 pixel 晚到:
    pixel 路径: coord(1拍) + linebuf(2拍) + corr(1拍) = 4拍
    is_bad 路径: coord(1拍) + bad_table(10拍) + corr(1拍) = 12拍

  需要在 pixel 路径上加 8 拍延迟来对齐 is_bad!
  或者重新设计让 bad_table 与 linebuf 并行:
  pixel 路径: coord(1拍) → linebuf(2拍) → 延迟对齐(8拍) → corr(1拍) = 12拍
  is_bad 路径: coord(1拍) → bad_table(10拍) → 延迟对齐(0拍) → corr(1拍) = 12拍

  总延迟: 12拍

# 技术约束
- 语言: Verilog
- 时钟频率: 125 MHz
- 总延迟: ≤12拍
- BRAM: 0块
- 遵循 awesom IP 接口标准

# 接口定义
```verilog
module dpc_top #(
    parameter C_DATA_WIDTH  = 10,
    parameter C_MAX_WIDTH   = 3840,
    parameter C_MAX_HEIGHT  = 2160,
    parameter C_ADDR_WIDTH  = 8,
    parameter C_CFG_DATA_WIDTH = 32
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [C_ADDR_WIDTH-1:0] cfg_addr,
    input  wire [C_CFG_DATA_WIDTH-1:0] cfg_wdata,
    input  wire        cfg_wen,
    input  wire        cfg_ren,
    output wire [C_CFG_DATA_WIDTH-1:0] cfg_rdata,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire [C_DATA_WIDTH-1:0] s_axis_tdata,
    input  wire        s_axis_tuser,
    input  wire        s_axis_tlast,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire [C_DATA_WIDTH-1:0] m_axis_tdata,
    output wire        m_axis_tuser,
    output wire        m_axis_tlast
);
```

# 输出要求
1. 完整的顶层模块Verilog代码
2. 子模块例化和连线
3. 流水线延迟对齐的移位寄存器实现
4. 延迟对齐的详细注释
```

---

## 使用说明

Phase 2 开发时，按以下顺序喂入提示词：
1. dpc_coord_tracker (最简单，先跑通)
2. dpc_linebuf (MVP版，简单)
3. dpc_correction (组合逻辑为主)
4. dpc_bad_table (最难，二分查找流水线)
5. dpc_reg (寄存器接口)
6. dpc_top (集成，延迟对齐是关键)

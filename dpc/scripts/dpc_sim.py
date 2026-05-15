import numpy as np

MAX_WIDTH = 3840
MAX_HEIGHT = 2160
DATA_WIDTH = 10
MAX_VAL = (1 << DATA_WIDTH) - 1


def get_bayer_color(x, y):
    if y % 2 == 0:
        return 'R' if x % 2 == 0 else 'Gr'
    else:
        return 'Gb' if x % 2 == 0 else 'B'


def is_same_color(x1, y1, x2, y2):
    return get_bayer_color(x1, y1) == get_bayer_color(x2, y2)


def generate_test_image(width=640, height=480):
    img = np.zeros((height, width), dtype=np.uint16)
    for y in range(height):
        for x in range(width):
            img[y, x] = (x * MAX_VAL // width + y * MAX_VAL // height) // 2
    return img


def generate_ramp_image(width=640, height=480):
    img = np.zeros((height, width), dtype=np.uint16)
    for y in range(height):
        for x in range(width):
            img[y, x] = (x * MAX_VAL) // width
    return img


def inject_bad_pixels(img, bad_pixel_list):
    corrupted = img.copy()
    for x, y, bad_type, bad_value in bad_pixel_list:
        if 0 <= y < img.shape[0] and 0 <= x < img.shape[1]:
            if bad_type == 'bright':
                corrupted[y, x] = MAX_VAL
            elif bad_type == 'dark':
                corrupted[y, x] = 0
            elif bad_type == 'offset':
                original = img[y, x]
                corrupted[y, x] = max(0, min(MAX_VAL, int(original) + bad_value))
            else:
                corrupted[y, x] = bad_value
    return corrupted


def dpc_correct(image, bad_pixel_set, use_line_buffer=True):
    h, w = image.shape
    corrected = image.copy()
    hit_count = 0

    for y in range(h):
        for x in range(w):
            if (x, y) not in bad_pixel_set:
                continue
            hit_count += 1

            neighbors = []

            if use_line_buffer:
                candidates = [
                    (x - 2, y),       # left
                    (x, y - 2),       # up
                    (x - 2, y - 2),   # up-left
                    (x + 2, y - 2),   # up-right
                ]
            else:
                candidates = [
                    (x - 2, y),       # left
                    (x, y - 2),       # up
                    (x + 2, y),       # right
                    (x, y + 2),       # down
                ]

            for nx, ny in candidates:
                if 0 <= nx < w and 0 <= ny < h:
                    if is_same_color(x, y, nx, ny):
                        neighbors.append(image[ny, nx])

            if neighbors:
                corrected[y, x] = int(sum(neighbors) / len(neighbors))

    return corrected, hit_count


def compare_results(original, corrupted, corrected):
    h, w = original.shape

    orig_bad = original.astype(np.int32)
    corr_bad = corrupted.astype(np.int32)
    fix_bad = corrected.astype(np.int32)

    mse_corrupted = np.mean((corr_bad - orig_bad) ** 2)
    mse_corrected = np.mean((fix_bad - orig_bad) ** 2)

    orig_flat = orig_bad.ravel()
    corr_flat = corr_bad.ravel()
    fix_flat = fix_bad.ravel()

    psnr_corrupted = 10 * np.log10(MAX_VAL ** 2 / max(mse_corrupted, 1))
    psnr_corrected = 10 * np.log10(MAX_VAL ** 2 / max(mse_corrected, 1))

    return {
        'mse_corrupted': float(mse_corrupted),
        'mse_corrected': float(mse_corrected),
        'psnr_corrupted': float(psnr_corrupted),
        'psnr_corrected': float(psnr_corrected),
    }


def generate_bad_pixel_list(num_bad=64, width=640, height=480, seed=42):
    import random
    random.seed(seed)
    bad_list = []
    types = ['bright', 'dark', 'offset']
    for _ in range(num_bad):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        bad_type = random.choice(types)
        if bad_type == 'offset':
            bad_value = random.randint(-200, 200)
        else:
            bad_value = 0
        bad_list.append((x, y, bad_type, bad_value))
    return bad_list


def generate_bad_pixel_set(bad_list):
    return {(x, y) for (x, y, _, _) in bad_list}


def print_histogram_stats(image, label):
    print(f"  [{label}]")
    print(f"    min={image.min()}, max={image.max()}")
    print(f"    mean={image.mean():.1f}, std={image.std():.1f}")


def main():
    print("=" * 60)
    print("  DPC (Defect Pixel Correction) 算法仿真")
    print("=" * 60)

    W, H = 640, 480
    print(f"\n1. 生成测试图像: {W}x{H}, RAW{DATA_WIDTH} Bayer RGGB")
    original = generate_test_image(W, H)
    print_histogram_stats(original, "原始图像")

    NUM_BAD = 64
    print(f"\n2. 注入 {NUM_BAD} 个坏点 (亮点/暗点/漂移点)")
    bad_list = generate_bad_pixel_list(NUM_BAD, W, H)
    bad_set = generate_bad_pixel_set(bad_list)
    corrupted = inject_bad_pixels(original, bad_list)
    print_histogram_stats(corrupted, "坏点图像")

    bright_count = sum(1 for _, _, t, _ in bad_list if t == 'bright')
    dark_count = sum(1 for _, _, t, _ in bad_list if t == 'dark')
    offset_count = sum(1 for _, _, t, _ in bad_list if t == 'offset')
    print(f"  亮点:{bright_count}  暗点:{dark_count}  漂移点:{offset_count}")

    print(f"\n3. 坏点校正 (FPGA约束模式: 仅用上行+左行邻域)")
    corrected, hit_count = dpc_correct(corrupted, bad_set, use_line_buffer=True)
    print(f"  命中坏点数: {hit_count}/{NUM_BAD}")
    print_histogram_stats(corrected, "校正后图像")

    print(f"\n4. 质量对比")
    metrics = compare_results(original, corrupted, corrected)
    print(f"  ┌──────────────┬──────────────┬──────────────┐")
    print(f"  │    指标       │   坏点图像     │   校正后图像    │")
    print(f"  ├──────────────┼──────────────┼──────────────┤")
    print(f"  │ MSE          │ {metrics['mse_corrupted']:>12.2f} │ {metrics['mse_corrected']:>12.2f} │")
    print(f"  │ PSNR (dB)    │ {metrics['psnr_corrupted']:>12.2f} │ {metrics['psnr_corrected']:>12.2f} │")
    print(f"  └──────────────┴──────────────┴──────────────┘")

    print(f"\n5. 坏点修复效果抽样 (前10个坏点)")
    print(f"  {'坐标':<12} {'类型':<8} {'原值':<6} {'坏值':<6} {'修复值':<6} {'误差':<6}")
    print(f"  {'-'*50}")
    for i, (bx, by, btype, bval) in enumerate(bad_list[:10]):
        ov = original[by, bx]
        cv = corrupted[by, bx]
        fv = corrected[by, bx]
        err = abs(int(fv) - int(ov))
        print(f"  ({bx:4d},{by:4d})  {btype:<8} {ov:<6d} {cv:<6d} {fv:<6d} {err:<6d}")

    avg_error = 0.0
    count = 0
    for bx, by, _, _ in bad_list:
        avg_error += abs(int(corrected[by, bx]) - int(original[by, bx]))
        count += 1
    avg_error /= max(count, 1)
    print(f"\n  平均修复误差: {avg_error:.2f} (共{count}个坏点)")

    print(f"\n6. 边界坏点测试")
    edge_bad_list = [
        (0, 0, 'bright', 0),
        (W - 1, 0, 'dark', 0),
        (0, H - 1, 'bright', 0),
        (W - 1, H - 1, 'dark', 0),
        (0, 100, 'offset', 300),
        (W - 1, 200, 'offset', -200),
    ]
    edge_set = generate_bad_pixel_set(edge_bad_list)
    edge_corrupted = inject_bad_pixels(original, edge_bad_list)
    edge_corrected, edge_hits = dpc_correct(edge_corrupted, edge_set, use_line_buffer=True)

    for bx, by, btype, _ in edge_bad_list:
        ov = original[by, bx]
        cv = edge_corrupted[by, bx]
        fv = edge_corrected[by, bx]
        same_neighbors = []
        for dy in [-2, 0, 2]:
            for dx in [-2, 0, 2]:
                nx, ny = bx + dx, by + dy
                if 0 <= nx < W and 0 <= ny < H and (nx, ny) != (bx, by):
                    if is_same_color(bx, by, nx, ny):
                        same_neighbors.append((nx, ny))
        print(f"  ({bx:4d},{by:4d}) {btype:<8}: 原={ov} 坏={cv} 修复={fv}  可用同色邻域={len(same_neighbors)}个")

    print(f"\n  DPC 算法仿真完成!")
    return 0


if __name__ == '__main__':
    exit(main())

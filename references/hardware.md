# 硬件编码器：能不能压、压得快不快

## 一句话结论

AV1 硬件编码是 2022 年底才铺开的，**只有较新的显卡有**。买卡或换机时最容易踩的坑是「能解码 ≠ 能编码」。

## AV1 硬件编码支持（2026 年现状）

| 厂商 | 有 AV1 硬件编码 | 只有解码、没有编码 |
|---|---|---|
| NVIDIA | RTX 40 系（Ada）及 50 系以上 | **RTX 30 系**（规格表写支持 AV1，指的是解码） |
| AMD | RX 7000 系（RDNA3）、RX 9000 系（RDNA4） | **RX 6000 系**；另外部分 RX 9000 精简型号干脆不带编码器，买前查具体型号 |
| Intel | Arc A/B 系独立显卡 | 多数核显只有解码，看具体型号与驱动 |

对应的 ffmpeg 编码器名：

| 编码器 | 厂商 |
|---|---|
| `av1_nvenc` | NVIDIA |
| `av1_amf` | AMD |
| `av1_qsv` | Intel |
| `av1_vaapi` | Linux 通用接口 |

## 没有 AV1 编码怎么办

**首选：退一档用 HEVC 硬编**。普及度高得多，损失几乎没有：

| 编码（同一 1080p 9 Mbps 源的 120 秒样本） | 体积 | VMAF |
|---|---|---|
| av1_amf qp35 | 56.6 MB | 96.20 |
| hevc_amf qp20 | 58.9 MB | 95.99 |

体积只大 4%、分数只低 0.2。而且 HEVC 的播放兼容性远好于 AV1（约 95% 设备 vs 60–70%），发给别人更能放。编码器名：`hevc_nvenc` / `hevc_amf` / `hevc_qsv`。

**次选：CPU 软编**，但要认清速度：

| 软编器 | 速度 | 结论 |
|---|---|---|
| `x265` medium 10bit | 1080p 约 0.4x（一部 3 小时片要 7 小时以上） | 批量场景不可用 |
| `libsvtav1` | 比 x265 快数倍 | 需要 FFmpeg 编译时带 SVT-AV1；官方精简版常常没有，换 BtbN 全量构建 |
| `libaom-av1` | 1–2 fps | 不可用 |

**最后：换思路**。加硬盘、去重、删不看的，都比软编划算。

## 探测命令

```bash
ffmpeg -hide_banner -encoders | grep -Ei "av1_(nvenc|amf|qsv|vaapi)|hevc_(nvenc|amf|qsv)|libsvtav1"
```

列出编码器只证明这个 ffmpeg 构建**包含**该后端，不证明本机能跑。必须真编码一次：

```bash
ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=30 -t 5 \
  -c:v av1_amf -rc cqp -qp_i 45 -y test.mp4 -loglevel error
```

（N 卡把 `av1_amf` 换 `av1_nvenc`，Intel 换 `av1_qsv`。）

## 速度与画质档

**一律用 `-quality speed`**：实测比 quality 档快 2.3–3.2 倍，VMAF 分毫不变。完整速度数据与逐场景数字见 `benchmarks.md` 第五节。
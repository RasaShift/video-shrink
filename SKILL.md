---
name: video-shrink
description: 批量压缩视频腾硬盘空间，或批量转码 AV1/HEVC；也覆盖「我的显卡能不能压 AV1」这类硬件咨询。触发场景：硬盘快满了想压片、视频太大、片库瘦身、换显卡前查 AV1 编码支持。含硬件编码器探测、抽样定档、VMAF 过线质检、批量脚本与验收口径。剪辑、字幕、单文件格式转换不归这里。
agent_created: true
---

# 视频批量瘦身

把码率过剩的视频重编码成 AV1 或 HEVC，用 30%–55% 的体积换视觉上难以分辨的画质。

## 何时用，何时别用

**用**：片库码率明显过剩——1080p 的 h264 在 5 Mbps 以上、老编码转录（mpeg4/wmv）、手机拍摄、录屏；机器有较新的硬件编码器。

**别用**，遇到这几种先劝退：

| 情况 | 为什么 |
|---|---|
| 源已经是 HEVC 或 AV1 | 二次压缩画质掉线，实测 HEVC 源再压 VMAF 只有 93.7，不及格 |
| 1080p 源低于 4 Mbps | 冗余本来就少，压不出空间，只会糊 |
| 还要剪辑、调色、做字幕 | 加工完再压，别在中间环节压 |
| 只有老显卡 | 软编慢到不可行（数据见 `references/hardware.md`） |

## 先给用户三条更省事的路

硬盘满了，压制排**第四**。先说：加硬盘或外置盘、去重（同一部片多份很常见）、删掉不看的。用户坚持再压。

## 硬规则

1. **原片只读**；删除动作只由用户发令，且发生在验收之后。
2. **有损不可逆**。VMAF ≥95 是「视觉上难辨」，不是无损。原片一删，画质回不来。
3. **参数不能照抄**。换分辨率档或换码率级（±2 Mbps）先抽 120 秒实测；已验证档位只作起点，见 `references/benchmarks.md`。
4. **一次只跑一批**（4K/2.5K 3 部、1080p 5 部），跑完报数等确认。VMAF 阶段 CPU 满载，开跑前先问用户机器空不空。
5. **逐部「压完即验」**：每部 VMAF 出了结论再跑下一部——中间有结论、掉线立刻停、不白花后续几部的电。
6. **首批全检**，之后才轮到抽样。抽样基线要用全检样本建。
7. **分档表没覆盖的分辨率（720p、1088p、低分辨率）交人工**：先抽 120 秒单独定档，禁止把 qp35/qp45 套上去。

## 前置：三样工具

开工前先确认这三样，缺哪样先装：

| 工具 | 干什么用 | 怎么拿 | 验证 |
|---|---|---|---|
| **FFmpeg** | 压制与质检的全部命令 | Windows 用 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 的 `essentials` 构建就够（含 AV1/HEVC 硬编）；要软编 SVT-AV1 则换 [BtbN 全量构建](https://github.com/BtbN/FFmpeg-Builds/releases) | `ffmpeg -version` 有输出；`ffmpeg -encoders` 里能查到 `av1_amf` / `av1_nvenc` / `av1_qsv` |
| **Python 3.8+** | 跑批量脚本 | python.org 或系统包管理器 | `python -V` |
| **播放器**（可选） | 验收成品 | PotPlayer / VLC / mpv | 能播 AV1 |

FFmpeg 解压到固定目录，把 `bin` 加进 PATH。**先装 FFmpeg 再谈压片**——没有它，下面的探测、抽样、批量全跑不了。

## 第一步 探硬件（不可跳过）

```bash
ffmpeg -hide_banner -encoders | grep -Ei "av1_(nvenc|amf|qsv|vaapi)|hevc_(nvenc|amf|qsv)|libsvtav1"
nvidia-smi --query-gpu=name,driver_version --format=csv   # 有 N 卡时
```

编码器出现在列表里 **≠** 本机能用——跑通一次 5 秒试编码才算数。

**头号坑：RTX 30 系和 RX 6000 系只有 AV1 解码、没有编码**。完整代际表与 HEVC 退路见 `references/hardware.md`。

## 第二步 抽样定档

从源的第 10 分钟抽 120 秒，用同一段做 VMAF 对比：

```bash
# 抽参考段
ffmpeg -ss 600 -t 120 -i "src.mp4" -c copy -y "seg_ref.mp4"
# 试编码
ffmpeg -hwaccel d3d11va -i "seg_ref.mp4" -c:v av1_amf -quality speed -rc cqp \
  -qp_i 35 -qp_p 35 -c:a copy -y "seg_av1.mp4"
# 质检（log_path 禁带盘符，见坑 1）
ffmpeg -i "seg_av1.mp4" -i "seg_ref.mp4" \
  -lavfi "[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[a][b]libvmaf=n_threads=12:log_fmt=json:log_path=vmaf.json" \
  -loglevel error -f null -
```

档位起点（实测来源见 `references/benchmarks.md`）：

| 源 | qp | 备注 |
|---|---|---|
| 1080p h264 5–10 Mbps | 35 + `-quality speed` | 实测 VMAF 96–99 |
| 4K / 2.5K 高码率（≥30 Mbps） | 45 + `-quality speed` | 码率过剩多，余量大 |
| 4K 源的 VMAF | — | 必须显式 `model=version=vmaf_4k_v0.6.1:n_subsample=3`，且只看均值（最低帧失真） |

判定：三段均值的最小值 ≥ 95 过线。选档要留 1 分余量，抽样段不一定是全片最难处。

## 第三步 批量跑

压制脚本在 `scripts/wb-encode.py`（探源→分档→压制→三段 VMAF→json 归档→测试段自清）。换机器时改文件顶部 4 个常量，脚本内有注释标出。也可以不装成技能，直接把它当独立 Python 脚本用。

```bash
python wb-encode.py "片1" "片2" "片3" --log run-log.tsv
```

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只探源和决策，不压。改完脚本必先跑这个 |
| `--vmaf-sample` | 抽样复检：同档前 3 部全检 → 每 5 抽 1 → 体积比偏离中位 >25% 强制补检 |
| `--log <tsv>` | 每部结果带时间戳追加，用于台账销账 |

## 第四步 验收与销账

1. 每部报一行：判定（PASS/FAIL）+ 成品体积 + 三段 VMAF
2. 状态四值：`待压` / `压完待验收` / `已验收` / `已删原片`
3. 成品改名回原名、删原片是常规收尾。脚本按**编码**判断是否已压（源是 av1 就 SKIP），不依赖 `_av1` 后缀
4. 播放器（PotPlayer 等）播放列表显示的编码可能是改名前的缓存，**以「播放信息」窗口的实时值为准**；判断依据是尺寸差（列表显示源尺寸，成品可能差几像素）

## 常见坑

1. **`libvmaf` 的 `log_path` 不能带盘符**。`C:/...` 会被 lavfi 的 `:` 当选项分隔符截断，报 Invalid argument。解法：把 ffmpeg 的 cwd 切到临时目录、`log_path` 用纯相对文件名，跑完再移动。
2. **`Popen` 的 stderr 接管道且循环里不读会死锁**。ffmpeg 日志写满缓冲（约 64KB）后进程永久阻塞，表现为「无输出 + 被杀」。必须 `-loglevel error` 并把 stderr 落到文件。
3. **二次压缩掉线**。压之前先 `ffprobe` 探源编码，HEVC/AV1 源直接放弃。
4. **最低帧不可信**。抽段用 `-c copy` 时首末帧对不齐，最低帧常给出 0 或极低值。判定只看均值，别为这个数字返工。
5. **串行跑批**。实测两路并行合计速度 ≈ 单路，串行反算还略快——机器时间花在顺序跑批上。

## 对外分享口径

分享方法、参数逻辑和脱敏数据，**不带任何源文件的名称、番号、目录、作者**。

`references/benchmarks.md` 里的数据已按这个口径脱敏：保留分辨率、码率、时长、编码器、显卡型号，去掉一切文件名。

对外必须同时讲清四条，缺一条就是误导：

1. 这是**有损**且不可逆，不是无损压缩
2. 已经是 HEVC/AV1 的源**别压**
3. 没有新显卡（RTX 40 / RX 7000 / Arc 起）基本干不了
4. 参数要按自己的源重测，不能照抄别处的数字

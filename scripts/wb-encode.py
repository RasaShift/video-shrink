# -*- coding: utf-8 -*-
# 本仓库即脚本最新版。改动请直接改这里并提交 PR / issue。
#
# 换机器要改的 4 个常量（在下面 CONFIG 区）：
#   FFMPEG / FFPROBE  —— ffmpeg 可执行文件路径
#   VMAF_ARCHIVE      —— VMAF json 归档根目录（建议跟成品放同一块盘）
#   N_THREADS         —— VMAF 线程数，取 CPU 逻辑核数的 3/4 左右（本机 16 线程取 12）
# 另外：编码器写死 av1_amf（AMD）。N 卡改 av1_nvenc、Intel 改 av1_qsv（qsv 需要加 -init_hw_device qsv）。
#       压制档位参数（nvenc 的 -preset p5、qsv 的 -preset fast）未实测，换编码器后先按
#       SKILL.md「第二步」抽 120 秒实测 VMAF 再定档，别直接套 qp 数值。
r"""
wb-encode.py — 批量压制脚本（AMF 硬编 AV1）

用法:
    python wb-encode.py <视频路径> [视频路径...] [--dry-run] [--vmaf-sample] [--log 结果.tsv]

流程（每部）:
    ffprobe 探分辨率/码率 -> 按分档表选 qp -> speed 档压制 -> 抽三段 VMAF 复检
    -> 过线判定 -> json 归档到归档目录\<名称>\ -> 清理测试段 -> 打印报告行

--log 结果.tsv:
    每部结果带时间戳追加一行（`时间 \\t 结果行`），供台账逐部销账用。
    只追加不覆盖；脚本不碰任何 .md 台账文件——表由人/agent 按这份日志改。

VMAF 复检频率（--vmaf-sample 才开，默认每部全检）:
    同档前 3 部全检（建立体积比中位数）；之后每 5 部抽 1 部；
    任一部成品体积比偏离该档中位数 >25% -> 该部强制补检（不吃抽样豁免）。
    依据：qp 固定时 VMAF 漂移来自源本身，体积比这个便宜指标能抓住异常源。

分档表:
    height >= 1440        -> qp45 + 4K 模型 VMAF(n_subsample=3)
    900 <= height < 1440  -> qp35 + 默认模型 VMAF(全帧)
    其他分辨率            -> SKIP 交人工（分档表未覆盖，不许猜）

边界（刻意不自动化）:
    - 不删原片（验收权在用户）
    - VMAF 不过线（<95）只标 FAIL，不自行降档重试
"""
import subprocess
import sys
import json
import shutil
import statistics
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent      # ffmpeg 所在目录（按实际改）
FFMPEG = TOOLS / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = TOOLS / "ffmpeg" / "bin" / "ffprobe.exe"
VMAF_ARCHIVE = Path(__file__).resolve().parent.parent / "vmaf_archive"   # 归档目录，按需改
LINE = 95.0                                         # 视觉无损线
N_THREADS = 12                                      # 10C16T 饱和值（libvmaf 2.0+ 默认 1，是慢的头号原因）


def probe(path: Path) -> dict:
    r = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration,size",
         "-show_entries", "stream=codec_type,codec_name,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {r.stderr[:200]}")
    d = json.loads(r.stdout)
    vs = next(s for s in d["streams"] if s["codec_type"] == "video")
    return {
        "dur": float(d["format"]["duration"]),
        "size": int(d["format"]["size"]),
        "w": vs.get("width", 0), "h": vs.get("height", 0),
        "vcodec": vs.get("codec_name", "?"),
        "streams": d["streams"],
    }


def pick(info: dict):
    """按分档表选参数。返回 (qp, vmaf_mode) 或 (None, 原因)。"""
    h = info["h"]
    if h >= 1440:
        return 45, "4k"
    if 900 <= h < 1440:
        return 35, "1080p"
    return None, f"分辨率 {info['w']}x{h} 不在分档表（仅覆盖 1080p 与 >=1440），交人工"


def encode(src: Path, out: Path, qp: int, dur: float) -> None:
    import tempfile
    import time
    prog = Path(tempfile.gettempdir()) / "_wbenc_prog.log"
    prog.unlink(missing_ok=True)
    cmd = [str(FFMPEG), "-hwaccel", "d3d11va", "-i", str(src),
           "-c:v", "av1_amf", "-quality", "speed", "-rc", "cqp",
           "-qp_i", str(qp), "-qp_p", str(qp),
           "-c:a", "copy", "-movflags", "+faststart",
           "-loglevel", "error", "-progress", str(prog), "-y", str(out)]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    start = time.time()
    last_report = 0.0
    last_pct = -1
    while p.poll() is None:
        time.sleep(5)
        elapsed = time.time() - start
        if elapsed - last_report >= 60 and prog.exists():   # 每 60 秒最多一行，且百分比必须真的变了
            try:
                txt = prog.read_text(encoding="utf-8", errors="ignore")
                us = [int(m) for m in __import__("re").findall(r"out_time_us=(\d+)", txt)]
                if us and dur > 0:
                    pct = int(us[-1] / 1e6 / dur * 100)
                    if pct != last_pct:                      # 同一百分比不重复刷行
                        last_report = elapsed
                        last_pct = pct
                        print(f"    ...{pct}%（已压 {us[-1]/6e7:.1f}/{dur/60:.1f} 分钟）", flush=True)
            except OSError:
                pass
    err = p.stderr.read()
    if p.returncode != 0:
        raise RuntimeError(f"压制失败: {err[-400:]}")


def extract_segments(src: Path, out: Path, dur: float) -> list:
    """从原片抽三段参考段（15%/50%/85% 起点），段长 60s，短片自动缩短。返回 [(段路径, 起点, 段长)]。"""
    seg_len = min(60.0, dur * 0.12)
    starts = [dur * 0.15, dur * 0.50, dur * 0.85]
    segs = []
    for i, ss in enumerate(starts, 1):
        seg = out.parent / f"_seg{i}.mp4"
        r = subprocess.run([str(FFMPEG), "-ss", f"{ss:.1f}", "-i", str(src),
                            "-t", f"{seg_len:.1f}", "-c", "copy",
                            "-loglevel", "error", "-y", str(seg)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"抽段 {i} 失败: {r.stderr[-200:]}")
        segs.append((seg, ss, seg_len))
    return segs


def vmaf(dist: Path, ref: Path, log: Path, mode: str, seg_dur: float, tag: str = "") -> dict:
    # lavfi 滤镜参数里 `:` 是选项分隔符——log_path 带盘符（C:/...）必被截断。
    # 解法：ffmpeg 的 cwd 切到临时目录，log_path 用纯相对文件名，跑完搬去归档。
    import tempfile
    import time
    tmp_dir = Path(tempfile.gettempdir())
    tmp_log = tmp_dir / "_vmaf_tmp.json"
    tmp_log.unlink(missing_ok=True)
    prog = tmp_dir / "_vmaf_prog.log"
    prog.unlink(missing_ok=True)
    if mode == "4k":
        f = (f"[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[a][b]"
             f"libvmaf=model=version=vmaf_4k_v0.6.1:n_threads={N_THREADS}"
             f":n_subsample=3:log_fmt=json:log_path=_vmaf_tmp.json")
    else:
        f = (f"[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[a][b]"
             f"libvmaf=n_threads={N_THREADS}:log_fmt=json:log_path=_vmaf_tmp.json")
    # VMAF 一段动辄 3 分钟（4K），全程阻塞等于又造一次「长时间没动静」——照样报进度。
    # stderr 必须落文件：Popen + 管道且循环里不读，缓冲写满会让 ffmpeg 永久阻塞（踩过）。
    errf = tmp_dir / "_vmaf_err.log"
    with open(errf, "w", encoding="utf-8", errors="ignore") as ef:
        p = subprocess.Popen([str(FFMPEG), "-i", str(dist), "-i", str(ref),
                              "-lavfi", f, "-progress", str(prog), "-nostats",
                              "-loglevel", "error", "-f", "null", "-"],
                             cwd=tmp_dir, stdout=subprocess.DEVNULL, stderr=ef)
        start = time.time()
        last_report = 0.0
        last_pct = -1
        while p.poll() is None:
            time.sleep(5)
            elapsed = time.time() - start
            if elapsed - last_report >= 30 and prog.exists():
                try:
                    txt = prog.read_text(encoding="utf-8", errors="ignore")
                    us = [int(m) for m in __import__("re").findall(r"out_time_us=(\d+)", txt)]
                    if us and seg_dur > 0:
                        pct = int(us[-1] / 1e6 / seg_dur * 100)
                        if pct != last_pct:
                            last_report = elapsed
                            last_pct = pct
                            print(f"    VMAF {tag}...{pct}%（{elapsed:.0f}s）", flush=True)
                except OSError:
                    pass
    if p.returncode != 0:
        raise RuntimeError(f"VMAF 失败: {errf.read_text(encoding='utf-8', errors='ignore')[-300:]}")
    log.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_log), str(log))
    frames = [fr["metrics"]["vmaf"] for fr in json.loads(log.read_text(encoding="utf-8"))["frames"]]
    return {"mean": sum(frames) / len(frames), "min": min(frames), "n": len(frames)}


def vmaf_plan(mode: str, state: dict):
    """抽样规则（仅 --vmaf-sample 生效）：前 3 部全检，之后每 5 部抽 1 部。"""
    st = state.setdefault(mode, {"n": 0, "ratios": []})
    st["n"] += 1
    n = st["n"]
    if n <= 3:
        return True, f"第{n}部(前3部全检)"
    if n % 5 == 1:
        return True, f"第{n}部(每5抽1)"
    return False, f"第{n}部(抽样跳过)"


def process(src: Path, dry: bool, state: dict, sample: bool = False) -> str:
    name = src.stem
    info = probe(src)
    # 用户验收后会把成品改名回原名（去掉 _av1 后缀），光看名字拦不住二次压缩。
    # 按编码判断：源已经是 AV1 就跳过——本机没有「AV1 再压 AV1」的场景。
    if info["vcodec"] == "av1":
        return f"SKIP  {name}: 已是 AV1 成品（按编码判断，防二次压缩）"
    qp, mode = pick(info)
    if qp is None:
        return f"SKIP  {name}: {mode}"
    out = src.parent / f"{name}_av1.mp4"
    line = (f"{name}: {info['w']}x{info['h']} {info['dur']/60:.0f}分钟 "
            f"{info['size']/1e9:.2f}GB -> qp{qp}+speed")
    if dry:
        plan, why = vmaf_plan(mode, state) if sample else (True, "全检")
        tail = "；体积比异常仍会补检" if sample and not plan else ""
        return f"DRY   {line}（dry-run，未压；复检：{'做' if plan else '跳过'} {why}{tail}）"

    plan, why = vmaf_plan(mode, state) if sample else (True, "全检")
    print(f"  压制中: {name} ...", flush=True)
    encode(src, out, qp, info["dur"])
    out_size = out.stat().st_size
    ratio = out_size / info["size"] * 100

    # 体积比异常 -> 抽样豁免失效，强制补检（前 3 部样本不足时也一律补）
    st = state.setdefault(mode, {"n": 0, "ratios": []})
    med = statistics.median(st["ratios"]) if len(st["ratios"]) >= 3 else None
    if sample and not plan:
        if med is None or abs(ratio - med) / med > 0.25:
            note = (f"体积比 {ratio:.1f}% 偏离中位 {med:.1f}% >25%，补检"
                    if med else "该档样本不足 3 部，补检")
            plan, why = True, note
            print(f"    ! {note}", flush=True)
    st["ratios"].append(ratio)

    if not plan:
        return (f"NOVMAF {line} -> {out_size/1e9:.2f}GB({ratio:.1f}%) | "
                f"复检跳过 {why} | 体积比中位 {med:.1f}%")

    segs = extract_segments(src, out, info["dur"])
    per_min = 3.0 if mode == "4k" else 0.6          # 粗估：4K 约 3 分钟/段，1080p 约 35 秒/段
    print(f"  VMAF 复检 {len(segs)} 段（CPU 将满载，约 {per_min*len(segs):.0f} 分钟）", flush=True)
    # 复检段：从成品抽同起点同长度的段作 dist
    scores = []
    archive = VMAF_ARCHIVE / name
    archive.mkdir(parents=True, exist_ok=True)
    for i, (seg_ref, ss, seg_len) in enumerate(segs, 1):
        dist_seg = out.parent / f"_seg{i}.d.mp4"
        r = subprocess.run([str(FFMPEG), "-ss", f"{ss:.1f}", "-i", str(out),
                            "-t", f"{seg_len:.1f}", "-c", "copy",
                            "-loglevel", "error", "-y", str(dist_seg)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"成品抽段 {i} 失败: {r.stderr[-200:]}")
        log = archive / f"vmaf_p{i}.json"
        sc = vmaf(dist_seg, seg_ref, log, mode, seg_len, tag=f"p{i}/{len(segs)}")
        scores.append(sc)
        dist_seg.unlink(missing_ok=True)

    # 收尾：删测试参考段（json 已含证据）
    for seg_ref, _ss, _sl in segs:
        seg_ref.unlink(missing_ok=True)

    means = [s["mean"] for s in scores]
    mins = [s["min"] for s in scores]
    verdict = "PASS" if min(means) >= LINE else "FAIL(<95, 交人工)"
    return (f"{verdict}  {line} -> {out_size/1e9:.2f}GB({ratio:.1f}%) | "
            f"VMAF {'/'.join(f'{m:.2f}' for m in means)} 最低帧 {min(mins):.1f} "
            f"| json 已归档 {archive}")


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    sample = "--vmaf-sample" in argv
    log_path = None
    if "--log" in argv:                              # 取走值，别让它被当成视频路径
        i = argv.index("--log")
        if i + 1 < len(argv):
            log_path = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)

    def emit(line):
        print(line, flush=True)
        if log_path:                                 # 逐部销账用；只追加，不改任何 md 台账
            from datetime import datetime
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{line}\n")

    state = {}                                       # 同档计数 + 体积比样本，跨本批文件共享
    for a in args:
        p = Path(a)
        if not p.exists():
            emit(f"SKIP  {p}: 文件不存在")
            continue
        if p.stem.endswith("_av1"):
            emit(f"SKIP  {p.name}: 已是压制产物")
            continue
        try:
            emit(process(p, dry, state, sample))
        except Exception as e:
            emit(f"ERROR {p.name}: {e}")


if __name__ == "__main__":
    main()

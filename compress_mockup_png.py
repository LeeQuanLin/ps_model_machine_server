"""无损压缩根目录下所有「样机图」文件夹中的 PNG。"""

import argparse
import os
import shutil
import stat
import struct
import sys
import tempfile
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHUNK_HEADER = struct.Struct(">I4s")
UINT32 = struct.Struct(">I")
BLOCK_SIZE = 1024 * 1024
MOCKUP_DIR = "样机图"


def read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("PNG 文件不完整")
    return data


def read_blocks(stream, size):
    while size:
        block = read_exact(stream, min(size, BLOCK_SIZE))
        size -= len(block)
        yield block


def write_idat(stream, data):
    if data:
        stream.write(CHUNK_HEADER.pack(len(data), b"IDAT"))
        stream.write(data)
        stream.write(UINT32.pack(zlib.crc32(data, zlib.crc32(b"IDAT"))))


def recompress_idat(source, target, header):
    # All IDAT chunks make up one zlib stream of already-filtered pixel data.
    inflater = zlib.decompressobj()
    deflater = zlib.compressobj(level=9)
    length, kind = CHUNK_HEADER.unpack(header)
    while kind == b"IDAT":
        crc = zlib.crc32(kind)
        for block in read_blocks(source, length):
            crc = zlib.crc32(block, crc)
            while block and not inflater.eof:
                pixels = inflater.decompress(block, BLOCK_SIZE)
                write_idat(target, deflater.compress(pixels))
                block = inflater.unconsumed_tail
        if read_exact(source, UINT32.size) != UINT32.pack(crc):
            raise ValueError("PNG 图像数据校验失败")
        header = read_exact(source, CHUNK_HEADER.size)
        length, kind = CHUNK_HEADER.unpack(header)

    while not inflater.eof and (pixels := inflater.decompress(b"", BLOCK_SIZE)):
        write_idat(target, deflater.compress(pixels))
    if not inflater.eof:
        raise ValueError("PNG 图像数据不完整")
    write_idat(target, deflater.flush())
    return header


def recompress_png(source, target):
    if read_exact(source, len(PNG_SIGNATURE)) != PNG_SIGNATURE:
        raise ValueError("文件不是 PNG")
    target.write(PNG_SIGNATURE)
    header = read_exact(source, CHUNK_HEADER.size)
    if CHUNK_HEADER.unpack(header) != (13, b"IHDR"):
        raise ValueError("PNG 缺少有效的文件头")
    image_data_seen = False
    while True:
        length, kind = CHUNK_HEADER.unpack(header)
        if kind == b"IDAT":
            if image_data_seen:
                raise ValueError("PNG 图像数据块不连续")
            header = recompress_idat(source, target, header)
            image_data_seen = True
            continue
        target.write(header)
        for block in read_blocks(source, length + UINT32.size):
            target.write(block)
        if kind == b"IEND":
            if not image_data_seen or length != 0:
                raise ValueError("PNG 缺少有效的图像数据或结束块")
            shutil.copyfileobj(source, target, BLOCK_SIZE)
            return
        header = read_exact(source, CHUNK_HEADER.size)


def compress_file(path):
    temporary = None
    try:
        with path.open("rb") as source:
            original = os.fstat(source.fileno())
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".mockup-png-", suffix=".tmp", delete=False,
            ) as target:
                temporary = Path(target.name)
                recompress_png(source, target)
                size = target.tell()
        if size >= original.st_size:
            return original.st_size, original.st_size

        current = path.stat(follow_symlinks=False)
        if (not os.path.samestat(original, current) or original.st_mtime_ns != current.st_mtime_ns
                or original.st_size != current.st_size):
            raise OSError("原文件在处理时已变化")
        if os.name != "nt":
            temporary.chmod(stat.S_IMODE(original.st_mode))
        os.utime(temporary, ns=(original.st_atime_ns, original.st_mtime_ns))
        temporary.replace(path)
        return original.st_size, size
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def mockup_images(root, onerror):
    for directory, children, files in os.walk(root, onerror=onerror):
        parent = Path(directory)
        # Skip links and junctions to avoid editing images outside this tree.
        children[:] = sorted(name for name in children if (parent / name).resolve() == parent / name)
        if root.name != MOCKUP_DIR and MOCKUP_DIR not in parent.relative_to(root).parts:
            continue
        for name in sorted(files):
            path = parent / name
            if path.suffix.lower() == ".png" and not path.is_symlink() and path.is_file():
                yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, metavar="根目录", help="解压后的产品总目录，或「样机图」文件夹")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"目录不存在：{root}")

    compressed = unchanged = failures = saved = 0

    def report_error(path, error):
        nonlocal failures
        failures += 1
        print(f"[失败] {path}：{error}", file=sys.stderr)

    def walk_error(error):
        report_error(error.filename, error.strerror)

    for image in mockup_images(root, walk_error):
        relative = image.relative_to(root)
        try:
            before, after = compress_file(image)
        except (OSError, ValueError, zlib.error) as exc:
            report_error(relative, exc)
            continue
        if before == after:
            unchanged += 1
            print(f"[跳过] {relative}，压缩后无法进一步缩小", flush=True)
        else:
            compressed += 1
            saved += before - after
            print(f"[已压缩] {relative} · {before / 2**20:.2f} → {after / 2**20:.2f} MiB", flush=True)

    if not (compressed or unchanged or failures):
        print("没有找到「样机图」文件夹中的 PNG。")
    print(f"完成。已压缩 {compressed} 张，跳过 {unchanged} 张，失败 {failures} 项，节省 {saved / 2**20:.2f} MiB。")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())

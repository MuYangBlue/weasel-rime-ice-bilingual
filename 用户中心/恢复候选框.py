"""移除临时候选框隐藏测试段，不覆盖其他配置。完成后需重新部署。"""
from pathlib import Path
import os


def restore(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    begin = "  # BEGIN temporary-hidden-candidate-test"
    end = "  # END temporary-hidden-candidate-test"
    if begin not in text and end not in text:
        return False
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError("测试配置标记异常，请勿自动覆盖配置")
    start = text.index(begin)
    stop = text.index(end)
    if stop < start:
        raise ValueError("测试配置标记顺序异常")
    stop += len(end)
    updated = text[:start] + text[stop:].lstrip("\r\n")
    temporary = path.with_suffix(path.suffix + ".restore.tmp")
    temporary.write_text(updated, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return True


if __name__ == "__main__":
    path = Path(__file__).resolve().parent.parent / "Rime" / "weasel.custom.yaml"
    try:
        changed = restore(path)
        print("已恢复候选框配置，请重新部署。" if changed else "没有需要移除的测试配置。")
    except (OSError, ValueError) as exc:
        print(f"恢复失败：{exc}")
    input("按回车关闭窗口……")

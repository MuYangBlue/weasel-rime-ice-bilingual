from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

def _locate_root() -> Path:
    """定位项目根目录，源码运行与 PyInstaller 打包运行都要能命中。

    源码运行时入口脚本在 用户中心/ 下，上一级即项目根；
    打包运行（PyInstaller onedir）时 onedir 内容被放进 _internal，
    入口脚本的 __file__ 会解析到
    用户中心/dist/小狼毫用户中心/_internal/main.py，
    按固定层数推导会错把 dist 子目录当成项目根，导致统计与部署全部读错位置。

    因此改为向上查找同时包含 Rime 目录的祖先，固定层数推导只作兜底。
    """
    if getattr(sys, "frozen", False):
        start = Path(sys.executable).resolve().parent
    else:
        start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / "Rime").is_dir():
            return candidate
    # 兜底：保持与旧行为一致，避免异常退出。
    return Path(__file__).resolve().parent.parent


ROOT = _locate_root()
RIME_DIR = ROOT / "Rime"
DATA_DIR = ROOT / "data"
IMPORT_DIR = ROOT / "import"
BACKUP_DIR = ROOT / "backup"
WEASEL_DIR = ROOT / "weasel-0.17.4"


def ensure_dirs() -> None:
    for path in (DATA_DIR, IMPORT_DIR, BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)


def open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))  # type: ignore[attr-defined]


def start_detached(command: list[str], cwd: Path | None = None) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(command, cwd=str(cwd) if cwd else None, creationflags=flags)


# ---------------------------------------------------------------------------
# 视觉规格：对齐参考实现（小说软件 WritingCalendar）的设计令牌。
# 参考稿是 Web UI，字号层级与色阶都是精调过的；Qt 默认控件观感松散，
# 所以这里把关键令牌抽成常量，统一由卡片工厂与自绘图表使用。
# ---------------------------------------------------------------------------
ACCENT = "#2563C9"       # 强调色：今日数字、激活页签、折线、热力
ACCENT_SOFT = "#E4EDFA"  # 强调色的淡底
LINE = "#C9C9C4"         # 1px 描边
TEXT = "#1B1C1E"         # 主文字
TEXT_2 = "#404246"       # 次级文字
TEXT_3 = "#5A5C62"       # 弱化文字
PANEL_RADIUS = 8         # 卡片圆角
MONO_STYLE = "font-variant-numeric: tabular-nums;"


HOURS = 24
SCHEMA_NAMES = {
    "rime_ice": "雾凇全拼",
    "double_pinyin_flypy": "小鹤双拼",
    "double_pinyin_sogou": "搜狗双拼",
}


def read_stats_rows() -> list[dict]:
    """读取每日统计。

    兼容三种历史格式：9 列（3.4.28 之前）、10 列（3.4.28 起）、
    11 列（3.4.30 起，多出 active_seconds）。缺失字段按 0 处理，不伪造历史数据。
    """
    stats_file = DATA_DIR / "input_stats.tsv"
    if not stats_file.exists():
        return []
    rows: list[dict] = []
    lines = stats_file.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
    for line in lines:
        fields = line.split("\t")
        if len(fields) < 9:
            continue
        hourly = [0] * HOURS
        if len(fields) >= 10 and fields[9].strip():
            for index, value in enumerate(fields[9].split(",")[:HOURS]):
                try:
                    hourly[index] = int(value)
                except ValueError:
                    hourly[index] = 0
        def as_int(value: str) -> int:
            try:
                return int(value)
            except ValueError:
                return 0
        rows.append(
            {
                "date": fields[0],
                "first_commit": fields[1],
                "last_commit": fields[2],
                "commits": as_int(fields[3]),
                "total_chars": as_int(fields[4]),
                "cjk_chars": as_int(fields[5]),
                "latin_chars": as_int(fields[6]),
                "digit_chars": as_int(fields[7]),
                "punct_chars": as_int(fields[8]),
                "hourly": hourly,
                "has_hourly": any(hourly),
                "active_seconds": as_int(fields[10]) if len(fields) >= 11 and fields[10].strip() else 0,
            }
        )
    return rows


def format_duration(seconds: int) -> str:
    """把秒数格式化为 HH:MM:SS，用于码字时长展示。"""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_trend(rows: list[dict], days: int) -> list[dict]:
    """按自然日回溯 days 天，缺失日期补 0。

    不能直接取「有记录的最近 N 行」：那样会把没写字的日子跳过去，
    断更三天在两个点之间看不出来，横轴也不再等距。
    """
    by_date = {row["date"]: row for row in rows}
    today = datetime.now().date()
    points: list[dict] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        key = day.strftime("%Y-%m-%d")
        row = by_date.get(key)
        points.append({"date": key, "words": row["total_chars"] if row else 0})
    return points


def summarize_range(rows: list[dict], days: int) -> dict:
    """统计最近 days 天的字数概况：平均、最高、累计。

    平均按区间天数计算（含没写字的日子），才反映真实产出节奏；
    参考实现 summarizeTrend 也是这个口径。
    """
    trend = build_trend(rows, days)
    if not trend:
        return {"average": 0, "peak": 0, "total": 0, "row": None}
    total = sum(point["words"] for point in trend)
    peak_point = max(trend, key=lambda point: point["words"])
    return {
        "average": round(total / len(trend)),
        "peak": peak_point["words"],
        "total": total,
        "row": peak_point,
    }


def month_summary(rows: list[dict], year: int, month: int) -> dict:
    """统计指定月份的总字数、累计时长、写作天数与最长连续写作天数。"""
    prefix = f"{year:04d}-{month:02d}"
    month_rows = [row for row in rows if row["date"].startswith(prefix)]
    total_chars = sum(row["total_chars"] for row in month_rows)
    active_seconds = sum(row.get("active_seconds", 0) for row in month_rows)
    writing_days = [row for row in month_rows if row["commits"] > 0]

    # 最长连续写作天数：按日期游标逐日推进，遇到没有记录的日子就断开。
    longest = 0
    current = 0
    previous: date | None = None
    for row in sorted(writing_days, key=lambda item: item["date"]):
        try:
            current_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if previous is not None and (current_date - previous).days == 1:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = current_date

    return {
        "total_chars": total_chars,
        "active_seconds": active_seconds,
        "writing_days": len(writing_days),
        "longest_streak": longest,
        "average": total_chars // len(writing_days) if writing_days else 0,
    }


def current_schema_name() -> str:
    """从 Rime/user.yaml 读取上次使用的方案，读不到就如实显示未知。"""
    user_yaml = RIME_DIR / "user.yaml"
    if not user_yaml.exists():
        return "未知"
    for line in user_yaml.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\s*previously_selected_schema:\s*(\S+)", line)
        if match:
            return SCHEMA_NAMES.get(match.group(1), match.group(1))
    return "未知"



class CalendarCell(QFrame):
    """日历格：日期与字数是两个独立元素，着色只作用于字数。

    日期占一行，不带底色；字数占下面一行，做成一个小圆角色块，
    底色按当日字数分 4 档叠加半透明强调色（8/18/30/44%），
    与参考实现「整块加深」的观感一致，但色块只包住数字本身。
    """

    clicked = Signal(str)

    def __init__(self, date_key: str, day: int, chars: int, seconds: int,
                 is_today: bool, heat: int) -> None:
        super().__init__()
        self._date_key = date_key
        self.setCursor(Qt.PointingHandCursor)
        self._base_style = (
            "QFrame { background: transparent; border: 1px solid transparent;"
            " border-radius: 6px; }"
        )
        self.setStyleSheet(self._base_style)
        self.setMinimumHeight(48)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 3, 2, 3)
        layout.setSpacing(2)

        # 日期：独立一行，无底色。
        day_label = QLabel(str(day))
        weight = "700" if is_today else "400"
        day_label.setStyleSheet(f"font-size: 12px; color: {TEXT_2}; font-weight: {weight};")
        day_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(day_label)

        # 字数：独立小色块，着色只对它生效。没有字数就不渲染色块。
        # 码字时长不再放进日历格，只在左侧今日卡显示。
        if chars:
            words_label = QLabel(f"{chars:,}")
            words_label.setAlignment(Qt.AlignCenter)
            words_label.setStyleSheet(self._words_style(chars, heat))
            layout.addWidget(words_label)

    @staticmethod
    def _words_style(chars: int, heat: int) -> str:
        """字数块样式：底色按热度分 4 档，字数越多底色越深。"""
        blends = {1: 0.08, 2: 0.18, 3: 0.30, 4: 0.44}
        color = QColor(ACCENT)
        blend = blends.get(heat, 0.0) if chars else 0.0
        return (
            f"QLabel {{ background: rgba({color.red()},{color.green()},{color.blue()},{blend});"
            f" border-radius: 4px; padding: 2px 4px; font-size: 11px;"
            f" color: {TEXT}; font-weight: 600; }}"
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(
            self._base_style.replace("border: 1px solid transparent;",
                                     f"border: 1px solid {ACCENT};")
        )

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._base_style)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._date_key)
        super().mouseReleaseEvent(event)


class HourChart(QWidget):
    """当日码字时段分布：按 24 小时柱状展示。

    数据来自统计文件的 hourly 列（每小时上屏字符数）。
    历史数据在 3.4.28 之前没有该字段，此时如实提示无明细，不画假的柱子。
    """

    def __init__(self) -> None:
        super().__init__()
        self._hours: list[int] = [0] * HOURS
        self.setMinimumHeight(96)

    def set_hours(self, hours: list[int]) -> None:
        self._hours = list(hours)[:HOURS] + [0] * max(0, HOURS - len(hours))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        peak = max(self._hours, default=0)
        if peak <= 0:
            painter.setPen(QPen(QColor(TEXT_3)))
            font = painter.font()
            font.setPixelSize(10)
            painter.setFont(font)
            painter.drawText(self.rect(), int(Qt.AlignCenter), "当日没有时段明细")
            painter.end()
            return

        left_gutter = 8
        bottom_gutter = 15
        # 顶部留 12px 给柱子上的字数标注，否则最高那根的数字会被裁掉。
        plot = QRectF(
            left_gutter,
            14,
            max(1.0, self.width() - left_gutter - 8),
            max(1.0, self.height() - bottom_gutter - 14),
        )
        slot = plot.width() / HOURS
        bar_width = max(2.0, slot * 0.62)

        painter.setPen(Qt.NoPen)
        for hour, value in enumerate(self._hours):
            if value <= 0:
                continue
            bar_height = plot.height() * (value / peak)
            x = plot.left() + slot * hour + (slot - bar_width) / 2
            y = plot.bottom() - bar_height
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(QRectF(x, y, bar_width, bar_height), 1.5, 1.5)

        # 柱子顶部标注该时段的字数，方便直接读数而不用对着纵轴估。
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(TEXT_2)))
        font = painter.font()
        font.setPixelSize(8)
        painter.setFont(font)
        for hour, value in enumerate(self._hours):
            if value <= 0:
                continue
            bar_height = plot.height() * (value / peak)
            y = plot.bottom() - bar_height
            painter.drawText(
                QRectF(plot.left() + slot * hour - slot / 2, y - 12, slot * 2, 11),
                int(Qt.AlignCenter),
                f"{value:,}",
            )

        painter.setPen(QPen(QColor(TEXT_3)))
        font = painter.font()
        font.setPixelSize(8)
        painter.setFont(font)
        # 24 个刻度全标，「0点 1点 … 23点」，8px 小字避免挤在一起。
        for hour in range(HOURS):
            x = plot.left() + slot * hour
            painter.drawText(
                QRectF(x, plot.bottom() + 1, slot, 13),
                int(Qt.AlignCenter),
                f"{hour}点",
            )
        painter.end()


class TrendChart(QWidget):
    """自绘码字趋势折线图。

    用它替代 QtCharts 的 QChart：QChart 自带图例、边框、动画与内边距，
    压不到参考实现那种紧凑克制的观感。这里手绘 5 条横向网格、左轴刻度、
    一条 1.6px 折线，与参考稿的 SVG 版本对应。
    """

    def __init__(self) -> None:
        super().__init__()
        self._points: list[int] = []

    def set_points(self, points: list[int]) -> None:
        self._points = list(points)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        left_gutter = 39
        top_pad = 4
        bottom_pad = 2
        plot = QRectF(
            left_gutter,
            top_pad,
            max(1.0, self.width() - left_gutter - 6),
            max(1.0, self.height() - top_pad - bottom_pad),
        )

        # 纵轴峰值向上取整到百位，无数据时按 1000，与参考实现一致。
        # 用显式向上取整，避免多次整除叠加把台阶算小、导致折线冲出顶部。
        peak = max(self._points, default=0)
        scale_max = max(peak, 1000)
        step = max(100, ((scale_max + 499) // 500) * 100)
        scale_top = step * 5

        painter.setPen(QPen(QColor(LINE), 0.5))
        for index in range(6):
            y = plot.top() + plot.height() * index / 5
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        painter.setPen(QPen(QColor(TEXT_3)))
        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        for index in range(6):
            value = scale_top - step * index
            y = plot.top() + plot.height() * index / 5
            painter.drawText(
                QRectF(0, y - 7, left_gutter - 7, 14),
                int(Qt.AlignRight | Qt.AlignVCenter),
                f"{value:,}",
            )

        if len(self._points) < 2:
            painter.end()
            return

        path = QPainterPath()
        for index, value in enumerate(self._points):
            x = plot.left() + plot.width() * index / (len(self._points) - 1)
            ratio = min(1.0, value / scale_top) if scale_top else 0.0
            y = plot.bottom() - plot.height() * ratio
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        pen = QPen(QColor(ACCENT), 1.6)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()


class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(130)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame { border: 2px dashed #8aa0bd; border-radius: 10px; }")
        layout = QVBoxLayout(self)
        self.label = QLabel("把词库文件拖到这里\n支持 TXT、TSV 和 Rime 词典")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("color: #687386; font-size: 15px;")
        layout.addWidget(self.label)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class MetricTile(QFrame):
    """单个指标小方块：上方是名称，下方是数值。

    用于「每日明细」区域：选中某个日期后，把该日的各指标并排铺成小方块，
    取代原来横向铺开的大表格。数值用等宽数字，避免刷新时宽度跳动。
    """

    def __init__(self, caption: str, value: str, accent: bool = False) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {ACCENT_SOFT if accent else 'transparent'};"
            f" border: 1px solid {LINE}; border-radius: {PANEL_RADIUS}px; }}"
        )
        self.setMinimumHeight(62)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        caption_label = QLabel(caption)
        caption_label.setStyleSheet(f"font-size: 11px; color: {TEXT_3}; border: none;")
        layout.addWidget(caption_label)

        value_label = QLabel(value)
        value_label.setStyleSheet(
            f"font-size: {'17' if accent else '15'}px;"
            f" color: {ACCENT if accent else TEXT}; font-weight: 600;"
            f" border: none; {MONO_STYLE}"
        )
        layout.addWidget(value_label)
        # 直接持有数值标签，避免靠 findChildren() 的返回顺序去猜哪个是数值。
        self._value_label = value_label

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)


class UserCenter(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        ensure_dirs()
        self.setWindowTitle("小狼毫用户中心")
        self.resize(980, 650)
        self._build_ui()
        self._build_tray()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("小狼毫用户中心")
        menu = QMenu()
        show = QAction("打开用户中心", self)
        show.triggered.connect(self.show_and_activate)
        menu.addAction(show)
        menu.addSeparator()
        deploy = QAction("重新部署", self)
        deploy.triggered.connect(self.deploy)
        menu.addAction(deploy)
        restart = QAction("重启算法服务", self)
        restart.triggered.connect(self.restart_service)
        menu.addAction(restart)
        open_root = QAction("打开工作区", self)
        open_root.triggered.connect(lambda: open_path(ROOT))
        menu.addAction(open_root)
        menu.addSeparator()
        quit_action = QAction("退出用户中心", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_and_activate() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def show_and_activate(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.tray.isVisible():
            self.hide()
            event.ignore()
        else:
            event.accept()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)

        self.nav = QListWidget()
        self.nav.setFixedWidth(190)
        self.nav.addItems(["首页", "输入方案", "词库管理", "双语功能", "输入统计", "系统维护"])
        layout.addWidget(self.nav)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._page_home())
        self.pages.addWidget(self._page_schemes())
        self.pages.addWidget(self._page_library())
        self.pages.addWidget(self._page_bilingual())
        self.pages.addWidget(self._page_stats())
        self.pages.addWidget(self._page_maintenance())
        layout.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.setCentralWidget(root)

    def _title(self, text: str, subtitle: str = "") -> QVBoxLayout:
        box = QVBoxLayout()
        title = QLabel(text)
        title.setStyleSheet("font-size: 26px; font-weight: 700;")
        box.addWidget(title)
        if subtitle:
            note = QLabel(subtitle)
            note.setStyleSheet("color: #687386; font-size: 14px;")
            box.addWidget(note)
        return box

    def _page_home(self) -> QWidget:
        page = QWidget()
        layout = self._title("小狼毫用户中心", "集中管理输入方案、词库、双语功能和输入统计")
        layout.addSpacing(24)
        status = QLabel("工作区：" + str(ROOT))
        status.setWordWrap(True)
        layout.addWidget(status)
        layout.addSpacing(16)
        cards = QHBoxLayout()
        today = datetime.now().strftime("%Y-%m-%d")
        today_chars = next((row["total_chars"] for row in read_stats_rows() if row["date"] == today), 0)
        for title, value in (
            ("当前方案", current_schema_name()),
            ("今日字数", f"{today_chars:,}"),
            ("数据目录", "已统一"),
            ("隐私模式", "不保存原文"),
        ):
            card = QFrame()
            card.setFrameShape(QFrame.StyledPanel)
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(QLabel(title))
            value_label = QLabel(value)
            value_label.setStyleSheet("font-size: 18px; font-weight: 600;")
            card_layout.addWidget(value_label)
            cards.addWidget(card)
        layout.addLayout(cards)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _page_schemes(self) -> QWidget:
        page = QWidget()
        layout = self._title("输入方案", "选择后会更新默认方案，并提交重新部署")
        layout.addSpacing(20)
        self.scheme_status = QLabel("可用方案")
        self.scheme_status.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self.scheme_status)
        available = {
            "全拼 · 雾凇拼音": "rime_ice",
            "双拼 · 小鹤双拼": "double_pinyin_flypy",
        }
        for label, schema_id in available.items():
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, sid=schema_id: self.set_default_schema(sid))
            layout.addWidget(button)
        missing_wubi = QLabel(
            "快捷键：Ctrl+1 全拼，Ctrl+2 小鹤双拼（仅在输入法拼字过程中生效，"
            "不会抢占浏览器的 Ctrl+1）。未拼字时可用 F4 或 Ctrl+` 打开方案选单。"
        )
        missing_wubi.setWordWrap(True)
        missing_wubi.setStyleSheet("color: #687386;")
        layout.addWidget(missing_wubi)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def set_default_schema(self, schema_id: str) -> None:
        path = RIME_DIR / "default.custom.yaml"
        text = path.read_text(encoding="utf-8")
        other_schema = "double_pinyin_flypy" if schema_id == "rime_ice" else "rime_ice"

        patch_match = re.search(r"(?m)^patch:[ \t]*$", text)
        if not patch_match:
            QMessageBox.warning(self, "无法切换", "配置中没有找到 patch 段，未做修改。")
            return
        head = text[: patch_match.end()]
        tail = text[patch_match.end():]

        # 只替换 schema_list 列表本身：不假设它紧跟 patch:
        # （实际文件里 key_binder 就排在它前面），也不会动同段内其它 patch 键。
        schema_block = (
            "  schema_list:\n"
            f"    - {{schema: {schema_id}}}\n"
            f"    - {{schema: {other_schema}}}\n"
        )
        # 末尾用 (?:\n|$) 而非单纯 \n：文件最后一行可能没有换行符，
        # 早先只匹配 \n 会让那条残留下来，和新块拼成三条且首条重复。
        tail, count = re.subn(
            r"(?m)^  schema_list:[ \t]*\n(?:    - .*(?:\n|$))*", schema_block, tail
        )
        if count == 0:
            # 漏写了 schema_list 时补上，而不是报错拒绝，避免按钮再次变成空操作。
            tail = "\n" + schema_block + tail.lstrip("\n")

        # 方案切换键统一为 when: composing，避免重新写成 when: always 抢占
        # 浏览器切标签、游戏快捷键等全局按键（这是方案被误切的根因）。
        updated = "".join(
            line.replace("when: always", "when: composing")
            if "select:" in line and "when: always" in line
            else line
            for line in (head + tail).splitlines(keepends=True)
        )

        backup = BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup / "default.custom.yaml")
        path.write_text(updated, encoding="utf-8", newline="\n")
        self.scheme_status.setText(f"已选择：{schema_id}，正在提交部署")
        self.deploy()

    def _page_bilingual(self) -> QWidget:
        page = QWidget()
        layout = self._title("双语功能", "管理候选释义显示和默认状态")
        layout.addSpacing(20)
        status = QLabel("当前默认：中文候选")
        status.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(status)
        chinese = QPushButton("设为默认中文")
        chinese.clicked.connect(lambda: self.set_bilingual_default(False, status))
        layout.addWidget(chinese)
        bilingual = QPushButton("设为默认双语")
        bilingual.clicked.connect(lambda: self.set_bilingual_default(True, status))
        layout.addWidget(bilingual)
        open_config = QPushButton("打开双语配置文件")
        open_config.clicked.connect(lambda: open_path(RIME_DIR / "rime_ice.custom.yaml"))
        layout.addWidget(open_config)
        note = QLabel("修改后需要重新部署；Lua 相关改动还需要重启算法服务。")
        note.setStyleSheet("color: #687386;")
        layout.addWidget(note)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def set_bilingual_default(self, enabled: bool, status: QLabel) -> None:
        path = RIME_DIR / "rime_ice.custom.yaml"
        text = path.read_text(encoding="utf-8")
        if enabled:
            if "    reset: 1" not in text:
                text = text.replace("    abbrev: [ 中, 英 ]\n", "    abbrev: [ 中, 英 ]\n    reset: 1\n", 1)
            status.setText("当前默认：双语候选")
        else:
            text = text.replace("    reset: 1\n", "", 1)
            status.setText("当前默认：中文候选")
        path.write_text(text, encoding="utf-8", newline="\n")

    def _page_placeholder(self, title: str, description: str) -> QWidget:
        page = QWidget()
        layout = self._title(title, description)
        layout.addSpacing(28)
        note = QLabel("功能入口已预留，后续会直接读写当前工作区配置。")
        note.setStyleSheet("color: #687386;")
        layout.addWidget(note)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _page_library(self) -> QWidget:
        page = QWidget()
        layout = self._title("词库管理", "把词库拖入工作区，稍后可转换并部署")
        layout.addSpacing(20)
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.import_files)
        layout.addWidget(self.drop_zone)
        open_import = QPushButton("打开导入目录")
        open_import.clicked.connect(lambda: open_path(IMPORT_DIR))
        layout.addWidget(open_import)
        open_data = QPushButton("打开数据目录")
        open_data.clicked.connect(lambda: open_path(DATA_DIR))
        layout.addWidget(open_data)
        convert = QPushButton("转换最近词库并部署")
        convert.clicked.connect(self.convert_latest_dictionary)
        layout.addWidget(convert)
        self.import_status = QLabel("尚未导入文件")
        self.import_status.setStyleSheet("color: #687386;")
        layout.addWidget(self.import_status)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def import_files(self, paths: list[str]) -> None:
        copied = []
        for source_name in paths:
            source = Path(source_name)
            if not source.is_file():
                continue
            target = IMPORT_DIR / source.name
            if target.exists():
                target = IMPORT_DIR / f"{source.stem}-{datetime.now():%Y%m%d-%H%M%S}{source.suffix}"
            shutil.copy2(source, target)
            copied.append(target.name)
        if copied:
            self.import_status.setText("已放入导入目录：" + "、".join(copied))

    def convert_latest_dictionary(self) -> None:
        sources = sorted(
            [*IMPORT_DIR.glob("*.txt"), *IMPORT_DIR.glob("*.tsv")],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not sources:
            QMessageBox.information(self, "没有词库", "请先拖入 TXT 或 TSV 词库文件。")
            return
        script = RIME_DIR / "tools" / "import_rime_dict.py"
        output = RIME_DIR / "imported_terms.dict.yaml"
        result = subprocess.run(
            [sys.executable, str(script), str(sources[0]), str(output), "--name", "imported_terms"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            QMessageBox.warning(self, "转换失败", result.stderr.strip() or "词库格式无法识别。")
            return
        self.import_status.setText(f"已转换：{sources[0].name}")
        self.deploy()

    def _panel(self, width: int | None = None) -> QFrame:
        """统一卡片外观：白底、1px 描边、8px 圆角。

        参考稿的卡片是扁平的细描边，Qt 的 StyledPanel 自带立体边框，
        观感偏旧，所以所有卡片都走这里，不再各写一套样式。
        """
        panel = QFrame()
        panel.setStyleSheet(
            f"QFrame {{ background: #FFFFFF; border: 1px solid {LINE};"
            f" border-radius: {PANEL_RADIUS}px; }}"
        )
        if width is not None:
            panel.setFixedWidth(width)
        return panel

    @staticmethod
    def _label(text: str, size: int, color: str = TEXT, bold: bool = False) -> QLabel:
        label = QLabel(text)
        weight = "font-weight: 700;" if bold else ""
        label.setStyleSheet(f"font-size: {size}px; color: {color}; {weight}")
        return label

    def _page_stats(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; }")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 18, 26, 22)
        outer.setSpacing(0)

        outer.addWidget(self._label("码字统计", 19, TEXT, bold=True))
        outer.addSpacing(14)

        self.stats_rows: list[dict] = []
        self.range_days = 30
        self.selected_day: str | None = None
        self.hour_view = False
        self.view_year, self.view_month = datetime.now().year, datetime.now().month

        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(self._build_today_card())
        top.addWidget(self._build_trend_panel(), 1)
        outer.addLayout(top)
        outer.addSpacing(14)

        middle = QHBoxLayout()
        middle.setSpacing(14)
        middle.addWidget(self._build_calendar_panel(), 1)
        middle.addWidget(self._build_month_panel())
        outer.addLayout(middle)
        outer.addSpacing(14)

        detail_panel = self._panel()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(12, 10, 12, 12)
        detail_layout.setSpacing(8)

        # 标题随选中日期变化，例如「11-15 当日明细」。
        self.detail_title = self._label("当日明细", 11, TEXT_3)
        detail_layout.addWidget(self.detail_title)

        # 指标方块：第一行只放四类字符构成，选中日期切换时只更新数值。
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(8)
        self.metric_tiles: dict[str, MetricTile] = {}
        for key, caption, accent in (
            ("cjk_chars", "中文", True),
            ("latin_chars", "英文", False),
            ("digit_chars", "数字", False),
            ("punct_chars", "标点", False),
        ):
            tile = MetricTile(caption, "—", accent)
            self.metric_tiles[key] = tile
            tiles_row.addWidget(tile, 1)
        detail_layout.addLayout(tiles_row)

        # 第二行：提交次数、活跃区间、高峰时段，信息偏长，单独放宽。
        tiles_row2 = QHBoxLayout()
        tiles_row2.setSpacing(8)
        for key, caption in (
            ("commits", "提交次数"),
            ("active_range", "活跃区间"),
            ("busiest", "高峰时段"),
        ):
            tile = MetricTile(caption, "—")
            self.metric_tiles[key] = tile
            tiles_row2.addWidget(tile, 1)
        detail_layout.addLayout(tiles_row2)

        outer.addWidget(detail_panel)

        outer.addSpacing(8)
        bottom = QHBoxLayout()
        self.stats_hint = self._label("", 11, TEXT_3)
        self.stats_hint.setWordWrap(True)
        bottom.addWidget(self.stats_hint, 1)
        refresh = QPushButton("刷新统计")
        refresh.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {LINE};"
            f" border-radius: 6px; padding: 5px 14px; color: {TEXT_2}; font-size: 12px; }}"
            f"QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}"
        )
        refresh.clicked.connect(self.refresh_stats)
        bottom.addWidget(refresh)
        outer.addLayout(bottom)

        self.refresh_stats()

        # 自动刷新：统计文件由输入法写入，界面不会自己感知变化。
        # 每 5 秒重读一次，让数字随打字更新，无需手动切页。
        # 用单个 QTimer 实例，重复进入统计页时先停掉旧的，避免叠加多个定时器。
        existing = getattr(self, "stats_timer", None)
        if existing is not None:
            existing.stop()
        self.stats_timer = QTimer(self)
        self.stats_timer.setInterval(5000)
        self.stats_timer.timeout.connect(self.refresh_stats_light)
        self.stats_timer.start()
        return page

    def _build_today_card(self) -> QWidget:
        card = self._panel(176)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(0)
        layout.addWidget(self._label("今日码字", 11, TEXT_3))
        layout.addSpacing(5)
        self.today_date_label = self._label("", 16, TEXT, bold=True)
        layout.addWidget(self.today_date_label)
        layout.addSpacing(10)

        main_row = QHBoxLayout()
        main_row.setSpacing(4)
        self.today_chars_label = self._label("0", 28, ACCENT, bold=True)
        main_row.addWidget(self.today_chars_label)
        unit = self._label("字", 13, TEXT_3)
        main_row.addWidget(unit)
        main_row.addStretch()
        layout.addLayout(main_row)
        layout.addSpacing(12)

        # 码字时长：标签与数值上下两行，数值放大到与日期同级，作为卡片的次级重点。
        layout.addWidget(self._label("今日码字时长", 11, TEXT_3))
        layout.addSpacing(2)
        self.today_duration_label = self._label("00:00:00", 16, TEXT, bold=True)
        layout.addWidget(self.today_duration_label)
        layout.addStretch()
        return card

    def _build_trend_panel(self) -> QWidget:
        card = self._panel()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(4)
        tab_wrap = QFrame()
        tab_wrap.setStyleSheet(
            f"QFrame {{ background: {LINE}; border: none; border-radius: 6px; }}"
        )
        tab_layout = QHBoxLayout(tab_wrap)
        tab_layout.setContentsMargins(3, 3, 3, 3)
        tab_layout.setSpacing(4)
        for days in (7, 15, 30):
            tab = QPushButton(f"{days}天")
            tab.setFlat(True)
            tab.setCursor(Qt.PointingHandCursor)
            tab.clicked.connect(lambda checked=False, d=days: self.set_range_days(d))
            tab_layout.addWidget(tab)
            setattr(self, f"range_tab_{days}", tab)
        head.addWidget(tab_wrap)
        head.addStretch()

        # 点日历某天时，这里从「趋势读数」换成「该日时辰读数 + 返回按钮」。
        self.range_stats_widgets: dict[str, QLabel] = {}
        self.trend_stats_box = QWidget()
        stats_row = QHBoxLayout(self.trend_stats_box)
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(16)
        for key, caption in (("average", "平均"), ("peak", "最高"), ("total", "累计")):
            box = QVBoxLayout()
            box.setSpacing(1)
            caption_label = self._label("", 10, TEXT_3)
            value_label = self._label("0", 15, TEXT, bold=True)
            box.addWidget(caption_label)
            box.addWidget(value_label)
            stats_row.addLayout(box)
            self.range_stats_widgets[key] = (caption_label, value_label)
        head.addWidget(self.trend_stats_box)

        # 时辰视图的读数与返回按钮，默认隐藏。
        self.hour_stats_box = QWidget()
        hour_stats_row = QHBoxLayout(self.hour_stats_box)
        hour_stats_row.setContentsMargins(0, 0, 0, 0)
        hour_stats_row.setSpacing(12)
        self.hour_title = self._label("", 12, TEXT, bold=True)
        hour_stats_row.addWidget(self.hour_title)
        self.hour_total = self._label("", 10, TEXT_3)
        hour_stats_row.addWidget(self.hour_total)
        back_button = QPushButton("返回趋势")
        back_button.setCursor(Qt.PointingHandCursor)
        back_button.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {LINE};"
            f" border-radius: 6px; padding: 3px 10px; color: {TEXT_2}; font-size: 11px; }}"
            f"QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}"
        )
        back_button.clicked.connect(self.clear_day_selection)
        hour_stats_row.addWidget(back_button)
        head.addWidget(self.hour_stats_box)
        self.hour_stats_box.setVisible(False)
        layout.addLayout(head)
        layout.addSpacing(10)

        # 图表区：折线图与时辰图叠在同一位置，按选择切换。
        self.trend_chart = TrendChart()
        self.trend_chart.setMinimumHeight(96)
        self.trend_chart.setMaximumHeight(118)
        layout.addWidget(self.trend_chart)

        self.hour_chart = HourChart()
        self.hour_chart.setMinimumHeight(96)
        self.hour_chart.setMaximumHeight(118)
        self.hour_chart.setVisible(False)
        layout.addWidget(self.hour_chart)
        layout.addSpacing(4)

        dates = QHBoxLayout()
        dates.setContentsMargins(39, 0, 0, 0)
        self.trend_start_label = self._label("", 9, TEXT_3)
        self.trend_end_label = self._label("", 9, TEXT_3)
        dates.addWidget(self.trend_start_label)
        dates.addStretch()
        dates.addWidget(self.trend_end_label)
        layout.addLayout(dates)
        layout.addStretch()
        return card

    def _build_calendar_panel(self) -> QWidget:
        card = self._panel()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(0)

        head = QHBoxLayout()
        tab = self._label("码字日历", 12, ACCENT, bold=True)
        tab.setStyleSheet(
            f"font-size: 12px; color: {ACCENT}; font-weight: 600;"
            f" background: {ACCENT_SOFT}; border-radius: 6px; padding: 4px 12px;"
        )
        head.addWidget(tab)
        head.addStretch()
        prev_button = QPushButton("◀")
        prev_button.setCursor(Qt.PointingHandCursor)
        prev_button.setFixedSize(22, 22)
        prev_button.setStyleSheet(
            f"QPushButton {{ border: 1px solid transparent; border-radius: 6px;"
            f" background: transparent; color: {TEXT_2}; font-size: 10px; }}"
            f"QPushButton:hover {{ border-color: {LINE}; color: {ACCENT}; }}"
        )
        prev_button.clicked.connect(lambda: self.shift_month(-1))
        head.addWidget(prev_button)
        self.calendar_title = self._label("", 13, TEXT, bold=True)
        head.addWidget(self.calendar_title)
        next_button = QPushButton("▶")
        next_button.setCursor(Qt.PointingHandCursor)
        next_button.setFixedSize(22, 22)
        next_button.setStyleSheet(prev_button.styleSheet())
        next_button.clicked.connect(lambda: self.shift_month(1))
        head.addWidget(next_button)
        layout.addLayout(head)
        layout.addSpacing(12)

        weekdays = QHBoxLayout()
        weekdays.setSpacing(3)
        for name in ("一", "二", "三", "四", "五", "六", "日"):
            label = self._label(name, 11, TEXT_3)
            label.setAlignment(Qt.AlignCenter)
            weekdays.addWidget(label, 1)
        layout.addLayout(weekdays)
        layout.addSpacing(4)

        self.calendar_grid = QGridLayout()
        self.calendar_grid.setSpacing(3)
        layout.addLayout(self.calendar_grid)
        layout.addSpacing(8)

        # 图例：对齐参考实现「少 → 达标」，说明底色深浅的含义。
        legend = QHBoxLayout()
        legend.setSpacing(4)
        legend.addWidget(self._label("少", 10, TEXT_3))
        for blend in (0.08, 0.18, 0.30, 0.44):
            swatch = QFrame()
            swatch.setFixedSize(12, 12)
            color = QColor(ACCENT)
            swatch.setStyleSheet(
                f"QFrame {{ background: rgba({color.red()},{color.green()},{color.blue()},{blend});"
                f" border-radius: 3px; }}"
            )
            legend.addWidget(swatch)
        legend.addWidget(self._label("多", 10, TEXT_3))
        legend.addSpacing(6)
        legend.addWidget(self._label("· 深浅按当日字数与当月最高值分档", 10, TEXT_3))
        legend.addStretch()
        layout.addLayout(legend)
        layout.addStretch()
        return card

    def _build_month_panel(self) -> QWidget:
        card = self._panel(230)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(0)

        self.month_title = self._label("本月码字总结", 13, TEXT, bold=True)
        layout.addWidget(self.month_title)
        layout.addSpacing(10)

        # 本月字数与时长并排两块，复用指标方块的样式，与下方明细区观感一致。
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        self.month_words_tile = MetricTile("本月字数", "0", accent=True)
        self.month_duration_tile = MetricTile("本月时长", "00:00:00")
        tiles.addWidget(self.month_words_tile, 1)
        tiles.addWidget(self.month_duration_tile, 1)
        layout.addLayout(tiles)
        layout.addSpacing(10)

        # 分隔线：把「本月」与「累计全部历史」两个口径在视觉上分开。
        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        rule.setStyleSheet(f"color: {LINE}; background-color: {LINE}; max-height: 1px; border: none;")
        layout.addWidget(rule)
        layout.addSpacing(8)

        # 码字总记录：标签居左、数值居右，逐行对齐。
        layout.addWidget(self._label("码字总记录", 11, TEXT_3))
        layout.addSpacing(4)
        self.month_total_labels: dict[str, QLabel] = {}
        for key, caption in (
            ("chars", "总字数"),
            ("seconds", "总时长"),
            ("days", "记录天数"),
        ):
            row = QHBoxLayout()
            row.addWidget(self._label(caption, 12, TEXT_2))
            row.addStretch()
            value = self._label("—", 12, TEXT, bold=True)
            value.setStyleSheet(f"font-size: 12px; color: {TEXT}; font-weight: 600; {MONO_STYLE}")
            row.addWidget(value)
            self.month_total_labels[key] = value
            layout.addLayout(row)
            layout.addSpacing(3)
        layout.addStretch()
        return card

    def set_range_days(self, days: int) -> None:
        self.range_days = days
        for candidate in (7, 15, 30):
            tab = getattr(self, f"range_tab_{candidate}")
            if candidate == days:
                tab.setStyleSheet(
                    f"QPushButton {{ background: {ACCENT}; color: #FFFFFF; border: none;"
                    f" border-radius: 6px; padding: 4px 12px; font-size: 12px; font-weight: 600; }}"
                )
            else:
                tab.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {TEXT_2}; border: none;"
                    f" border-radius: 6px; padding: 4px 12px; font-size: 12px; }}"
                )
        self._refresh_trend()
        # 点区间页签意味着想看趋势，所以从时辰视图切回折线图。
        if getattr(self, "hour_view", False):
            self._show_hour_view(False)

    def shift_month(self, delta: int) -> None:
        month = self.view_month + delta
        year = self.view_year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self.view_year, self.view_month = year, month
        self._refresh_calendar()
        self._refresh_month_panel()

    def refresh_stats(self) -> None:
        """重新读取统计文件，刷新今日卡、趋势图、日历与明细表。"""
        self.stats_rows = read_stats_rows()
        self._refresh_today()
        self.set_range_days(self.range_days)
        self._refresh_calendar()
        self._refresh_month_panel()
        self._refresh_table()
        self._refresh_hint()

    def refresh_stats_light(self) -> None:
        """定时器用的轻量刷新：只更新数字，不重建控件。

        完整的 refresh_stats 会清空并重建整月日历格子（约 30 个控件），
        每 5 秒执行一次会闪烁且浪费。这里跳过日历重建与区间切换，
        只把随时间变化的数字重新填一遍。
        """
        self.stats_rows = read_stats_rows()
        self._refresh_today()
        self._refresh_trend()
        self._refresh_month_panel()
        self._refresh_table()
        self._refresh_hint()
        # 时辰分布若正在显示，也要跟着更新（选中的日期不变）。
        if self.hour_view:
            self._refresh_hour_chart()

    def _refresh_today(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        row = next((item for item in self.stats_rows if item["date"] == today), None)
        chars = row["total_chars"] if row else 0
        seconds = row.get("active_seconds", 0) if row else 0
        self.today_date_label.setText(today[5:].replace("-", "/"))
        self.today_chars_label.setText(f"{chars:,}")
        self.today_duration_label.setText(format_duration(seconds))

    def _refresh_trend(self) -> None:
        summary = summarize_range(self.stats_rows, self.range_days)
        for key, caption in (("average", "平均"), ("peak", "最高"), ("total", "累计")):
            caption_label, value_label = self.range_stats_widgets[key]
            caption_label.setText(f"{self.range_days}日{caption}")
            value_label.setText(f"{summary[key]:,}")
        trend = build_trend(self.stats_rows, self.range_days)
        self.trend_chart.set_points([point["words"] for point in trend])
        if trend:
            self.trend_start_label.setText(trend[0]["date"][5:])
            self.trend_end_label.setText(trend[-1]["date"][5:])

    def _refresh_calendar(self) -> None:
        while self.calendar_grid.count():
            item = self.calendar_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        year, month = self.view_year, self.view_month
        self.calendar_title.setText(f"{year}年{month:02d}月")
        by_date = {
            row["date"]: row
            for row in self.stats_rows
            if row["date"].startswith(f"{year:04d}-{month:02d}")
        }
        peak = max((row["total_chars"] for row in by_date.values()), default=0)

        import calendar as _calendar

        first_weekday, days_in_month = _calendar.monthrange(year, month)
        row_index = 0
        column = first_weekday  # Python 周一为 0，正好对上表头。
        today = datetime.now().strftime("%Y-%m-%d")
        for day in range(1, days_in_month + 1):
            key = f"{year:04d}-{month:02d}-{day:02d}"
            data = by_date.get(key)
            chars = data["total_chars"] if data else 0
            seconds = data.get("active_seconds", 0) if data else 0
            cell = CalendarCell(
                key, day, chars, seconds, key == today, self._heat_ratio(chars, peak)
            )
            cell.clicked.connect(self.select_day)
            self.calendar_grid.addWidget(cell, row_index, column)
            column += 1
            if column > 6:
                column = 0
                row_index += 1

        # 默认展示今天；若今天不在当前月，则展示当月第一天。
        if self.selected_day is None or not self.selected_day.startswith(f"{year:04d}-{month:02d}"):
            self.selected_day = today if today.startswith(f"{year:04d}-{month:02d}") else f"{year:04d}-{month:02d}-01"
        self._refresh_hour_chart()

    def select_day(self, date_key: str) -> None:
        """点日历某天：上方图表区切换成该日的时辰分布，下方方块换成该日指标。"""
        self.selected_day = date_key
        self._show_hour_view(True)
        self._refresh_table()

    def clear_day_selection(self) -> None:
        """返回趋势视图。"""
        self._show_hour_view(False)

    def _show_hour_view(self, show_hours: bool) -> None:
        """在上方图表区切换「折线趋势」与「当日时辰分布」。

        两者占用同一块位置，所以切换时同步显隐，避免卡片高度跳动。
        """
        self.hour_view = show_hours
        self.trend_stats_box.setVisible(not show_hours)
        self.hour_stats_box.setVisible(show_hours)
        self.trend_chart.setVisible(not show_hours)
        self.hour_chart.setVisible(show_hours)
        # 横轴首尾日期只在趋势视图下有意义。
        self.trend_start_label.setVisible(not show_hours)
        self.trend_end_label.setVisible(not show_hours)
        if show_hours:
            self._refresh_hour_chart()

    def _refresh_hour_chart(self) -> None:
        date_key = self.selected_day or datetime.now().strftime("%Y-%m-%d")
        row = next((item for item in self.stats_rows if item["date"] == date_key), None)
        hours = row["hourly"] if row else [0] * HOURS
        self.hour_title.setText(f"{date_key[5:]} 时段分布（24 小时）")
        if row and row["total_chars"]:
            self.hour_total.setText(
                f"{row['total_chars']:,} 字 · {format_duration(row.get('active_seconds', 0))}"
            )
        else:
            self.hour_total.setText("无记录")
        self.hour_chart.set_hours(hours)

    @staticmethod
    def _heat_ratio(chars: int, peak: int) -> int:
        """按当日字数相对当月最高值返回 0-4 档热度。"""
        if chars <= 0 or peak <= 0:
            return 0
        ratio = chars / peak
        if ratio >= 0.8:
            return 4
        if ratio >= 0.55:
            return 3
        if ratio >= 0.3:
            return 2
        return 1


    def _refresh_month_panel(self) -> None:
        summary = month_summary(self.stats_rows, self.view_year, self.view_month)
        # 上排两块：只统计当前查看的月份。
        self.month_words_tile.set_value(f"{summary['total_chars']:,}")
        self.month_duration_tile.set_value(format_duration(summary["active_seconds"]))

        # 下排三行：累计本地统计文件以来的全部历史，切换月份不受影响。
        total_chars = sum(row["total_chars"] for row in self.stats_rows)
        total_seconds = sum(row.get("active_seconds", 0) for row in self.stats_rows)
        self.month_total_labels["chars"].setText(f"{total_chars:,} 字")
        self.month_total_labels["seconds"].setText(format_duration(total_seconds))
        self.month_total_labels["days"].setText(f"{len(self.stats_rows)} 天")

    def _refresh_table(self) -> None:
        """把选中日期的各指标填进方块。

        原来的多日表格已改为「点日历某天 → 该日指标小方块」。
        找不到选中日期的记录时显示破折号，不伪造 0。
        """
        date_key = self.selected_day or datetime.now().strftime("%Y-%m-%d")
        row = next((item for item in self.stats_rows if item["date"] == date_key), None)
        self.detail_title.setText(f"{date_key[5:]} 当日明细")

        if row is None:
            for tile in self.metric_tiles.values():
                tile.set_value("—")
            return

        self.metric_tiles["cjk_chars"].set_value(f"{row['cjk_chars']:,}")
        self.metric_tiles["latin_chars"].set_value(f"{row['latin_chars']:,}")
        self.metric_tiles["digit_chars"].set_value(f"{row['digit_chars']:,}")
        self.metric_tiles["punct_chars"].set_value(f"{row['punct_chars']:,}")
        self.metric_tiles["commits"].set_value(f"{row['commits']:,}")
        self.metric_tiles["active_range"].set_value(
            f"{row['first_commit']} - {row['last_commit']}"
        )
        self.metric_tiles["busiest"].set_value(self._busiest_range(row["hourly"]))

    def _refresh_hint(self) -> None:
        """底部提示：只在完全没有数据时给一句引导，其余情况不显示文字。"""
        if not self.stats_rows:
            self.stats_hint.setText("还没有统计数据，用输入法打几个字后回来刷新。")
            self.stats_hint.setVisible(True)
        else:
            self.stats_hint.setText("")
            self.stats_hint.setVisible(False)

    @staticmethod
    def _busiest_range(hourly: list[int]) -> str:
        if not any(hourly):
            return "—"
        top = sorted(range(len(hourly)), key=lambda index: hourly[index], reverse=True)[:2]
        return "、".join(f"{hour:02d}时" for hour in sorted(top))

    def _page_maintenance(self) -> QWidget:
        page = QWidget()
        layout = self._title("系统维护", "所有操作都针对当前工作区")
        layout.addSpacing(20)
        deploy = QPushButton("重新部署")
        deploy.clicked.connect(self.deploy)
        layout.addWidget(deploy)
        restart = QPushButton("重启算法服务")
        restart.clicked.connect(self.restart_service)
        layout.addWidget(restart)
        backup = QPushButton("备份当前配置")
        backup.clicked.connect(self.backup)
        layout.addWidget(backup)
        open_root = QPushButton("打开工作区")
        open_root.clicked.connect(lambda: open_path(ROOT))
        layout.addWidget(open_root)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def deploy(self) -> None:
        exe = WEASEL_DIR / "WeaselDeployer.exe"
        if not exe.exists():
            QMessageBox.warning(self, "无法部署", f"找不到：{exe}")
            return
        start_detached([str(exe), "/deploy"], WEASEL_DIR)
        QMessageBox.information(self, "已提交", "部署命令已提交，请等待托盘提示完成。")

    def restart_service(self) -> None:
        server = WEASEL_DIR / "WeaselServer.exe"
        if server.exists():
            start_detached([str(server), "/q"], WEASEL_DIR)
        start_detached([str(WEASEL_DIR / "start_service.bat")], WEASEL_DIR)
        QMessageBox.information(self, "已提交", "算法服务重启命令已提交。")

    def backup(self) -> None:
        target = BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(RIME_DIR, target / "Rime", ignore=shutil.ignore_patterns("build", "*.userdb", "*.userdb/*"))
        shutil.copytree(DATA_DIR, target / "data", dirs_exist_ok=True)
        QMessageBox.information(self, "备份完成", f"已保存到：\n{target}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("小狼毫用户中心")
    window = UserCenter()
    window.show()
    sys.exit(app.exec())

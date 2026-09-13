"""查 DeepSeek 余额。

接口：GET https://api.deepseek.com/user/balance
     Authorization: Bearer <KEY>
返回：
     {"is_available": true,
      "balance_infos": [{"currency": "CNY", "total_balance": "110.00",
                         "granted_balance": "10.00", "topped_up_balance": "100.00"}]}

放线程里查 —— 网络卡住时不能把界面冻住。这是唯一一个会阻塞的地方。
"""
from PyQt5.QtCore import QThread, pyqtSignal
import requests

API_URL = "https://api.deepseek.com/user/balance"
TIMEOUT = 8


class BalanceFetcher(QThread):
    """查一次就结束。要刷新就再 new 一个。"""

    ok = pyqtSignal(str)      # 已经排版好的余额文本
    fail = pyqtSignal(str)    # 给人看的错误说明

    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self.api_key = api_key

    def run(self) -> None:
        if not self.api_key:
            self.fail.emit("还没设置 API Key\n右键「设置 API Key」")
            return
        try:
            r = requests.get(
                API_URL,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Accept": "application/json"},
                timeout=TIMEOUT,
            )
        except requests.Timeout:
            self.fail.emit("查询超时（网络不通？）")
            return
        except requests.RequestException as e:
            self.fail.emit(f"网络错误：{type(e).__name__}")
            return

        if r.status_code == 401:
            self.fail.emit("API Key 无效")
            return
        if r.status_code != 200:
            self.fail.emit(f"接口返回 {r.status_code}")
            return

        try:
            self.ok.emit(format_balance(r.json()))
        except (ValueError, KeyError, TypeError) as e:
            self.fail.emit(f"返回内容看不懂：{e}")


def format_balance(data: dict) -> str:
    infos = data.get("balance_infos") or []
    if not infos:
        return "余额：无可用账户" if not data.get("is_available") else "余额：0.00"
    parts = []
    for info in infos:
        cur = info.get("currency", "")
        total = info.get("total_balance", "?")
        sym = {"CNY": "¥", "USD": "$"}.get(cur, "")
        parts.append(f"{sym}{total}" + (f" {cur}" if not sym else ""))
    text = "  ".join(parts)
    return text if data.get("is_available", True) else f"{text}（余额不足）"

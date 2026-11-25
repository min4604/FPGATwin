# C++ 模擬執行環境（Harness）

本目錄將放置與 Verilator 產生的模型連結的 C++ 執行環境：

- 以模擬時間（ps）為基準的多時脈事件排程器
- 依 `board.yaml` 驅動輸入腳（開關、按鈕）、讀取輸出腳（LED）
- UART 收發：以 TCP socket 對外橋接（Windows 可搭配 com0com 虛擬序列埠）
- 與前端 GUI 的通訊介面

尚未實作。

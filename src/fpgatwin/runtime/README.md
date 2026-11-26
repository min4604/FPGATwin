# C++ 模擬執行環境（Harness）

目前的 harness 由 `fpgatwin/rtl/harness.cpp.in` 樣板依每個設計自動產生，見 `fpgatwin/rtl/build.py`：

- 以模擬時間（ps）為基準的多時脈排程器，邊緣時間以整數精確計算、不累積誤差
- 透過 stdin/stdout 與 Python 端溝通（協定行以 `\x1e` 開頭，與設計的 `$display` 輸出區隔）

- UART（USB-UART 橋接晶片）的腳位層級模型也在 harness 內：依模擬時間解碼設計的 TX、驅動 RX

本目錄保留給之後需要獨立 C++ 原始碼的周邊模型（例如 SPI Flash、IIC EEPROM）。

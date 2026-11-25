// SP601 UART echo 範例：開機（或放開 CPU_RESET）後送出一行歡迎訊息，
// 之後把收到的每個字元原樣送回；LED 顯示最後收到字元的低 4 位元。
// 115200 baud, 8N1，接板上的 CP2103 USB-UART（L12 = FPGA TX, K14 = FPGA RX）。

module uart_echo #(
    parameter integer CLK_HZ = 27_000_000,
    parameter integer BAUD   = 115_200
) (
    input  wire       USER_CLOCK,
    input  wire       CPU_RESET,
    input  wire       UART_RX,
    output wire       UART_TX,
    output wire [3:0] GPIO_LED
);
    localparam integer CLKS_PER_BIT = CLK_HZ / BAUD;  // 234（略快於 234.375，回傳不會積壓）
    localparam integer MSG_LEN = 22;
    localparam [8*MSG_LEN-1:0] MSG = "Hello from FPGATwin!\015\012";

    wire       rx_valid;
    wire [7:0] rx_data;
    wire       tx_busy;
    reg        tx_start = 1'b0;
    reg  [7:0] tx_data  = 8'd0;

    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_rx (
        .clk(USER_CLOCK), .rst(CPU_RESET), .rx(UART_RX), .valid(rx_valid), .data(rx_data));
    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_tx (
        .clk(USER_CLOCK), .rst(CPU_RESET), .start(tx_start), .data(tx_data), .tx(UART_TX),
        .busy(tx_busy));

    reg [4:0] idx     = 5'd0;   // next greeting character
    reg       pending = 1'b0;   // a received byte waits to be echoed
    reg [7:0] pend    = 8'd0;
    reg [3:0] led     = 4'd0;

    always @(posedge USER_CLOCK) begin
        tx_start <= 1'b0;
        if (CPU_RESET) begin
            idx     <= 5'd0;
            pending <= 1'b0;
            led     <= 4'd0;
        end else begin
            if (!tx_busy && !tx_start) begin
                if (idx < MSG_LEN) begin
                    tx_data  <= MSG[8*(MSG_LEN-1-idx) +: 8];
                    tx_start <= 1'b1;
                    idx      <= idx + 5'd1;
                end else if (pending) begin
                    tx_data  <= pend;
                    tx_start <= 1'b1;
                    pending  <= 1'b0;
                end
            end
            if (rx_valid) begin  // after the send block, so a new byte is never lost
                pend    <= rx_data;
                pending <= 1'b1;
                led     <= rx_data[3:0];
            end
        end
    end

    assign GPIO_LED = led;
endmodule

// 8N1 UART 傳送端：start 為一個時脈的脈衝，busy 期間忽略新的 start。

module uart_tx #(
    parameter integer CLKS_PER_BIT = 234
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output reg        tx = 1'b1,
    output wire       busy
);
    localparam [15:0] LAST = CLKS_PER_BIT[15:0] - 16'd1;

    reg        active  = 1'b0;
    reg [3:0]  bit_idx = 4'd0;   // 0 = start bit, 1..8 = data bits, 9 = stop bit
    reg [15:0] cnt     = 16'd0;
    reg [7:0]  shreg   = 8'd0;

    assign busy = active;

    always @(posedge clk) begin
        if (rst) begin
            active <= 1'b0;
            tx     <= 1'b1;
        end else if (!active) begin
            if (start) begin
                active  <= 1'b1;
                shreg   <= data;
                bit_idx <= 4'd0;
                cnt     <= 16'd0;
                tx      <= 1'b0;  // start bit
            end
        end else if (cnt == LAST) begin
            cnt <= 16'd0;
            if (bit_idx == 4'd9) begin
                active <= 1'b0;
            end else begin
                bit_idx <= bit_idx + 4'd1;
                if (bit_idx < 4'd8) begin
                    tx    <= shreg[0];  // LSB first
                    shreg <= shreg >> 1;
                end else begin
                    tx <= 1'b1;         // stop bit
                end
            end
        end else begin
            cnt <= cnt + 16'd1;
        end
    end
endmodule

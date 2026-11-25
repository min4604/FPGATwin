// 8N1 UART 接收端：在每個位元中間取樣；停止位元正確時輸出一個時脈的 valid。

module uart_rx #(
    parameter integer CLKS_PER_BIT = 234
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,
    output reg        valid = 1'b0,
    output reg  [7:0] data  = 8'd0
);
    localparam [15:0] FULL = CLKS_PER_BIT[15:0] - 16'd1;
    localparam [15:0] HALF = CLKS_PER_BIT[16:1] - 16'd1;

    reg [1:0]  sync    = 2'b11;  // two-flop synchroniser, idle high
    reg        active  = 1'b0;
    reg [3:0]  bit_idx = 4'd0;   // 0 = start bit, 1..8 = data bits, 9 = stop bit
    reg [15:0] cnt     = 16'd0;
    reg [7:0]  shreg   = 8'd0;

    wire r = sync[1];

    always @(posedge clk) sync <= {sync[0], rx};

    always @(posedge clk) begin
        valid <= 1'b0;
        if (rst) begin
            active <= 1'b0;
        end else if (!active) begin
            if (!r) begin
                active  <= 1'b1;
                bit_idx <= 4'd0;
                cnt     <= 16'd0;
            end
        end else if (cnt == ((bit_idx == 4'd0) ? HALF : FULL)) begin
            cnt <= 16'd0;
            if (bit_idx == 4'd0) begin
                if (r) active <= 1'b0;  // glitch, not a start bit
                else bit_idx <= 4'd1;
            end else if (bit_idx <= 4'd8) begin
                shreg   <= {r, shreg[7:1]};
                bit_idx <= bit_idx + 4'd1;
            end else begin
                active <= 1'b0;
                if (r) begin
                    valid <= 1'b1;
                    data  <= shreg;
                end
            end
        end else begin
            cnt <= cnt + 16'd1;
        end
    end
endmodule

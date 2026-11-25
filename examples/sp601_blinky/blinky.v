// SP601 blinky 範例：27 MHz USER_CLOCK 驅動 4 顆 LED 做二進位計數，
// DIP 開關 SW8.1 打開時暫停計數，CPU_RESET 按鈕清除。
// 純行為寫法，不需要任何 FPGA 原語。

module blinky #(
    parameter integer CNT_BITS = 26   // LED0 = counter[22]：27 MHz / 2^23 ≈ 3.2 Hz 閃爍
) (
    input  wire       USER_CLOCK,
    input  wire       CPU_RESET,
    /* verilator lint_off UNUSEDSIGNAL */  // 只用到 SW8.1，其餘保留給使用者擴充
    input  wire [3:0] GPIO_SWITCH,
    /* verilator lint_on UNUSEDSIGNAL */
    output wire [3:0] GPIO_LED
);

    // FPGA 上電時暫存器會載入初始值，因此同時保留初始值與 reset 是常見寫法
    /* verilator lint_off PROCASSINIT */
    reg [CNT_BITS-1:0] counter = {CNT_BITS{1'b0}};
    /* verilator lint_on PROCASSINIT */

    always @(posedge USER_CLOCK) begin
        if (CPU_RESET)
            counter <= {CNT_BITS{1'b0}};
        else if (!GPIO_SWITCH[0])
            counter <= counter + 1'b1;
    end

    assign GPIO_LED = counter[CNT_BITS-1 -: 4];

endmodule

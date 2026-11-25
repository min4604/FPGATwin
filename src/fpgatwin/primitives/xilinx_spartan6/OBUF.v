// FPGATwin clean-room simulation model: output buffer (I -> O).
/* verilator lint_off UNUSEDPARAM */
module OBUF #(
    parameter IOSTANDARD = "DEFAULT",
    parameter SLEW = "SLOW",
    parameter DRIVE = 12
) (
    output wire O,
    input  wire I
);
    assign O = I;
endmodule

// FPGATwin clean-room simulation model: input buffer (I -> O).
/* verilator lint_off UNUSEDPARAM */
module IBUF #(
    parameter IOSTANDARD = "DEFAULT"
) (
    output wire O,
    input  wire I
);
    assign O = I;
endmodule

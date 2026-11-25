// FPGATwin clean-room simulation model: clock input buffer (I -> O).
/* verilator lint_off UNUSEDPARAM */
module IBUFG #(
    parameter IOSTANDARD = "DEFAULT"
) (
    output wire O,
    input  wire I
);
    assign O = I;
endmodule

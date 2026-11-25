// FPGATwin clean-room simulation model: differential clock input buffer.
// Written from scratch from the publicly documented port list (I, IB -> O).
// Functional model only: output follows the P input.
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNUSEDPARAM */
module IBUFGDS #(
    parameter DIFF_TERM = "FALSE",
    parameter IOSTANDARD = "DEFAULT"
) (
    output wire O,
    input  wire I,
    input  wire IB
);
    assign O = I;
endmodule

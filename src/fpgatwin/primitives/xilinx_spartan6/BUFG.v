// FPGATwin clean-room simulation model: global clock buffer (I -> O).
module BUFG (
    output wire O,
    input  wire I
);
    assign O = I;
endmodule

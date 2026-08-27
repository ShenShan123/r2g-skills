module reset_restore_b (
    input  wire clk,
    input  wire rst_n,
    input  wire launch,
    output reg  complete
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            complete <= 1'b1; // BUG: reset must clear complete
        else if (launch)
            complete <= 1'b1;
        else
            complete <= 1'b0;
    end
endmodule

// 8-bit up/down counter with synchronous load and enable (r2g demo design).
module counter8 (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       en,
    input  wire       up,
    input  wire       load,
    input  wire [7:0] d,
    output reg  [7:0] q,
    output wire       wrap
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)      q <= 8'd0;
        else if (load)   q <= d;
        else if (en)     q <= up ? q + 8'd1 : q - 8'd1;
    end
    assign wrap = en & (up ? (q == 8'hff) : (q == 8'h00));
endmodule

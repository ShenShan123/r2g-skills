module future_parametric_logic_v4 (
    input        clk,
    input        rst_n,
    input        en,
    input  [7:0] a,
    input  [7:0] b,
    output reg [7:0] y,
    output reg       valid
);
  always @(posedge clk) begin
    if (!rst_n) begin
      y     <= 8'h00;
      valid <= 1'b0;
    end else if (en) begin
      y     <= (a + b) ^ {a[3:0], b[7:4]};
      valid <= 1'b1;
    end else begin
      valid <= 1'b0;
    end
  end
endmodule

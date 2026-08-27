module reset_restore_c(
    input wire clk,
    input wire rst_n,
    input wire start,
    output reg finished
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      // Bug: reset asserts completion instead of clearing it.
      finished <= 1'b1;
    end else if (start) begin
      finished <= 1'b1;
    end
  end
endmodule

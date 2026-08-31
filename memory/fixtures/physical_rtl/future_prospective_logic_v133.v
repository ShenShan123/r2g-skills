module future_prospective_logic_v133 (
    input wire clk, input wire rst_n, input wire capture,
    input wire [7:0] lhs, input wire [7:0] rhs,
    output reg [8:0] chosen, output reg greater
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin chosen <= 9'h000; greater <= 1'b0; end
    else if (capture) begin
      greater <= lhs > rhs;
      chosen <= (lhs > rhs) ? ({1'b0, lhs} + 9'h011)
                            : ({1'b0, rhs} ^ 9'h053);
    end else greater <= 1'b0;
  end
endmodule

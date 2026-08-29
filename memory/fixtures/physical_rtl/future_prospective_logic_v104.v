module future_prospective_logic_v104 (
    input wire clk, input wire rst_n, input wire capture,
    input wire [7:0] lhs, input wire [7:0] rhs,
    output reg [7:0] chosen, output reg greater
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin chosen <= 8'h00; greater <= 1'b0; end
    else if (capture) begin
      greater <= lhs > rhs;
      chosen <= (lhs > rhs) ? (lhs ^ 8'h6d) : (rhs + 8'h23);
    end else greater <= 1'b0;
  end
endmodule

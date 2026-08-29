module future_prospective_logic_v98 (
    input wire clk, input wire rst_n, input wire capture,
    input wire [7:0] lhs, input wire [7:0] rhs,
    output reg [9:0] sum, output reg done
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin sum <= 10'h000; done <= 1'b0; end
    else begin
      done <= capture;
      if (capture) sum <= {2'b00, lhs} + {2'b00, rhs} + 10'h065;
    end
  end
endmodule

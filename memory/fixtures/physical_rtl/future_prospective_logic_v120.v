module future_prospective_logic_v120 (
    input wire clk, input wire rst_n, input wire request,
    input wire [7:0] payload, output reg [8:0] encoded, output reg ready
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin encoded <= 9'h000; ready <= 1'b0; end
    else begin
      ready <= request;
      if (request) encoded <= {payload, 1'b0} ^ 9'h12d;
    end
  end
endmodule

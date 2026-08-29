module future_prospective_logic_v105 (
    input wire clk, input wire rst_n, input wire request,
    input wire [7:0] payload, output reg [7:0] stored, output reg ready
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin stored <= 8'h00; ready <= 1'b0; end
    else begin
      ready <= request;
      if (request) stored <= {payload[5:0], payload[7:6]} ^ 8'h87;
    end
  end
endmodule

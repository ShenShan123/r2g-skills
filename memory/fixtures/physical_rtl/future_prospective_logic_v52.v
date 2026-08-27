module future_prospective_logic_v52 (
    input wire clk, input wire rst_n, input wire request, input wire [7:0] payload,
    output reg [7:0] stored, output reg ready
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin stored <= 8'h00; ready <= 1'b0; end
    else begin ready <= request; if (request) stored <= payload + 8'h15; end
  end
endmodule

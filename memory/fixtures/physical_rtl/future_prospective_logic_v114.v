module future_prospective_logic_v114 (
    input wire clk, input wire rst_n, input wire request,
    input wire [7:0] payload, output reg [7:0] result, output reg ready
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin result <= 8'h00; ready <= 1'b0; end
    else begin
      ready <= request;
      if (request) result <= {payload[2:0], payload[7:3]} ^ 8'ha1;
    end
  end
endmodule

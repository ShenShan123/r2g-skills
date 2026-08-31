module future_prospective_logic_v130 (
    input wire clk, input wire rst_n, input wire request,
    input wire [7:0] payload, output reg [9:0] result, output reg ready
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin result <= 10'h000; ready <= 1'b0; end
    else begin
      ready <= request;
      if (request) result <= ({2'b00, payload} << 1) + 10'h05b;
    end
  end
endmodule

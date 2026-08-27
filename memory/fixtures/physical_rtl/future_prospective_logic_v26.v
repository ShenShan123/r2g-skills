module future_prospective_logic_v26 (
    input wire clk,
    input wire rst_n,
    input wire request,
    input wire [15:0] value,
    output reg [15:0] stored,
    output reg ready
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      stored <= 16'h0000;
      ready <= 1'b0;
    end else begin
      ready <= request;
      if (request)
        stored <= value ^ 16'h0f0f;
    end
  end
endmodule

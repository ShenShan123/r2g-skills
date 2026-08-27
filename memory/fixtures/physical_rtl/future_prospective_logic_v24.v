module future_prospective_logic_v24 (
    input wire clk,
    input wire rst_n,
    input wire valid,
    input wire ready,
    input wire [15:0] payload,
    output reg [15:0] result,
    output reg done
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      result <= 16'h0000;
      done <= 1'b0;
    end else begin
      done <= valid & ready;
      if (valid & ready)
        result <= payload + 16'h003d;
    end
  end
endmodule

module future_prospective_logic_v20 (
    input         clk,
    input         rst_n,
    input         step,
    input  [7:0]  data_in,
    output reg [7:0] count,
    output reg       hit
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      count <= 8'h00;
      hit <= 1'b0;
    end else if (step) begin
      count <= count + data_in;
      hit <= (count + data_in) > 8'hc0;
    end
  end
endmodule

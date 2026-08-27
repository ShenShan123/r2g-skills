module v2_arbiter16(
    input wire clk,
    input wire rst,
    input wire en,
    input wire [15:0] request,
    output reg [15:0] grant,
    output reg [3:0] owner
);
  integer i;
  reg found;
  always @(posedge clk) begin
    if (rst) begin
      grant <= 16'b0;
      owner <= 4'b0;
    end else if (en) begin
      grant <= 16'b0;
      found = 1'b0;
      for (i = 0; i < 16; i = i + 1) begin
        if (!found && request[(owner + i) & 4'b1111]) begin
          grant[(owner + i) & 4'b1111] <= 1'b1;
          owner <= (owner + i + 1'b1) & 4'b1111;
          found = 1'b1;
        end
      end
    end
  end
endmodule

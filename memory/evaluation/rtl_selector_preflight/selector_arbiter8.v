module selector_arbiter8(
    input wire clk,
    input wire rst,
    input wire en,
    input wire [7:0] request,
    output reg [7:0] grant,
    output reg [2:0] owner
);
  integer i;
  reg found;
  always @(posedge clk) begin
    if (rst) begin
      grant <= 8'b0;
      owner <= 3'b0;
    end else if (en) begin
      grant <= 8'b0;
      found = 1'b0;
      for (i = 0; i < 8; i = i + 1) begin
        if (!found && request[(owner + i) & 3'b111]) begin
          grant[(owner + i) & 3'b111] <= 1'b1;
          owner <= (owner + i + 1'b1) & 3'b111;
          found = 1'b1;
        end
      end
    end
  end
endmodule

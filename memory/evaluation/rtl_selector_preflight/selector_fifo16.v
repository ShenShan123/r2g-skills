module selector_fifo16(
    input wire clk,
    input wire rst,
    input wire push,
    input wire pop,
    input wire [15:0] din,
    output reg [15:0] dout,
    output reg full,
    output reg empty
);
  reg [15:0] storage [0:15];
  reg [3:0] wr_ptr;
  reg [3:0] rd_ptr;
  reg [4:0] level;
  always @(posedge clk) begin
    if (rst) begin
      wr_ptr <= 4'b0;
      rd_ptr <= 4'b0;
      level <= 5'b0;
      dout <= 16'b0;
    end else begin
      if (push && !full) begin
        storage[wr_ptr] <= din;
        wr_ptr <= wr_ptr + 1'b1;
      end
      if (pop && !empty) begin
        dout <= storage[rd_ptr];
        rd_ptr <= rd_ptr + 1'b1;
      end
      case ({push && !full, pop && !empty})
        2'b10: level <= level + 1'b1;
        2'b01: level <= level - 1'b1;
        default: level <= level;
      endcase
    end
  end
  always @* begin
    full = (level == 5'd16);
    empty = (level == 5'd0);
  end
endmodule

module v2_cal_shift32(
    input wire clk,
    input wire rst,
    input wire en,
    input wire [1:0] mode,
    input wire [31:0] data,
    output reg [31:0] result
);
  always @(posedge clk) begin
    if (rst) result <= 32'b0;
    else if (en) begin
      case (mode)
        2'b00: result <= {data[30:0], 1'b0};
        2'b01: result <= {1'b0, data[31:1]};
        2'b10: result <= {data[15:0], data[31:16]};
        default: result <= data ^ {data[15:0], data[31:16]};
      endcase
    end
  end
endmodule

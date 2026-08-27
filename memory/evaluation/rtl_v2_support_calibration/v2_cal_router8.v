module v2_cal_router8(
    input wire clk,
    input wire rst,
    input wire valid,
    input wire [2:0] port,
    input wire [7:0] payload,
    output reg [7:0] lane0,
    output reg [7:0] lane1,
    output reg [7:0] lane2,
    output reg [7:0] lane3
);
  always @(posedge clk) begin
    if (rst) begin
      lane0 <= 8'b0;
      lane1 <= 8'b0;
      lane2 <= 8'b0;
      lane3 <= 8'b0;
    end else if (valid) begin
      case (port[1:0])
        2'b00: lane0 <= payload;
        2'b01: lane1 <= payload;
        2'b10: lane2 <= payload;
        default: lane3 <= payload;
      endcase
    end
  end
endmodule

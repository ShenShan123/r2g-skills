module v2_packet_ctrl(
    input wire clk,
    input wire rst,
    input wire start,
    input wire done,
    input wire error,
    output reg busy,
    output reg accepted,
    output reg fault
);
  localparam IDLE = 2'b00;
  localparam RUN = 2'b01;
  localparam FAIL = 2'b10;
  reg [1:0] state;
  always @(posedge clk) begin
    if (rst) begin
      state <= IDLE;
      busy <= 1'b0;
      accepted <= 1'b0;
      fault <= 1'b0;
    end else begin
      accepted <= 1'b0;
      case (state)
        IDLE: if (start) begin state <= RUN; busy <= 1'b1; end
        RUN: if (error) begin state <= FAIL; busy <= 1'b0; fault <= 1'b1; end
             else if (done) begin state <= IDLE; busy <= 1'b0; accepted <= 1'b1; end
        FAIL: if (start) begin state <= RUN; busy <= 1'b1; fault <= 1'b0; end
        default: begin state <= IDLE; busy <= 1'b0; end
      endcase
    end
  end
endmodule

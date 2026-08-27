module selector_uart16(
    input wire clk,
    input wire rst,
    input wire load,
    input wire tick,
    input wire [7:0] tx_data,
    output reg tx,
    output reg busy
);
  reg [3:0] bit_index;
  reg [9:0] shift_reg;
  always @(posedge clk) begin
    if (rst) begin
      bit_index <= 4'd0;
      shift_reg <= 10'b1111111111;
      tx <= 1'b1;
      busy <= 1'b0;
    end else begin
      if (load && !busy) begin
        shift_reg <= {1'b1, tx_data, 1'b0};
        bit_index <= 4'd0;
        busy <= 1'b1;
      end else if (tick && busy) begin
        tx <= shift_reg[0];
        shift_reg <= {1'b1, shift_reg[9:1]};
        if (bit_index == 4'd9) busy <= 1'b0;
        else bit_index <= bit_index + 1'b1;
      end
    end
  end
endmodule

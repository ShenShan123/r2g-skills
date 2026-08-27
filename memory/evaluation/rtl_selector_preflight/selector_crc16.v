module selector_crc16(
    input wire clk,
    input wire rst,
    input wire valid,
    input wire [7:0] data,
    output reg [15:0] crc
);
  integer j;
  reg [15:0] next_crc;
  always @* begin
    next_crc = crc ^ {data, 8'b0};
    for (j = 0; j < 8; j = j + 1) begin
      if (next_crc[15]) next_crc = {next_crc[14:0], 1'b0} ^ 16'h1021;
      else next_crc = {next_crc[14:0], 1'b0};
    end
  end
  always @(posedge clk) begin
    if (rst) crc <= 16'hffff;
    else if (valid) crc <= next_crc;
  end
endmodule

module v2_crc32(
    input wire clk,
    input wire rst,
    input wire valid,
    input wire [7:0] data,
    output reg [31:0] crc
);
  integer j;
  reg [31:0] next_crc;
  always @* begin
    next_crc = crc ^ {data, 24'b0};
    for (j = 0; j < 8; j = j + 1) begin
      if (next_crc[31]) next_crc = {next_crc[30:0], 1'b0} ^ 32'h04c11db7;
      else next_crc = {next_crc[30:0], 1'b0};
    end
  end
  always @(posedge clk) begin
    if (rst) crc <= 32'hffffffff;
    else if (valid) crc <= next_crc;
  end
endmodule

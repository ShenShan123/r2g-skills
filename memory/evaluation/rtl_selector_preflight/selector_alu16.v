module selector_alu16(
    input wire clk,
    input wire rst,
    input wire en,
    input wire [2:0] opcode,
    input wire [15:0] a,
    input wire [15:0] b,
    output reg [31:0] result,
    output reg zero
);
  reg [31:0] next_result;
  always @* begin
    case (opcode)
      3'b000: next_result = a + b;
      3'b001: next_result = a - b;
      3'b010: next_result = a & b;
      3'b011: next_result = a | b;
      3'b100: next_result = a ^ b;
      3'b101: next_result = a * b;
      3'b110: next_result = {a, b};
      default: next_result = 32'b0;
    endcase
  end
  always @(posedge clk) begin
    if (rst) begin
      result <= 32'b0;
      zero <= 1'b1;
    end else if (en) begin
      result <= next_result;
      zero <= (next_result == 32'b0);
    end
  end
endmodule

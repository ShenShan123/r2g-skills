module future_prospective_logic_v60 (
    input wire clk, input wire rst_n, input wire push, input wire [7:0] value,
    output reg [7:0] held, output reg full
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin held <= 8'h00; full <= 1'b0; end
    else if (push) begin held <= value ^ 8'h3c; full <= 1'b1; end
    else full <= 1'b0;
  end
endmodule

module future_prospective_logic_v89 (
    input wire clk, input wire load, input wire [7:0] payload,
    output reg [11:0] state, output reg observed
);
  always @(posedge clk) begin
    observed <= load;
    if (load) state <= (state << 1) ^ {4'ha, payload} ^ 12'h2d7;
  end
endmodule

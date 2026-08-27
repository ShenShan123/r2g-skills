// Independent SPI-shaped serial bridge lineage.
// Port cardinality matches the held-out interface, but the implementation is
// a synchronous edge tracker with separate input/output rings and parity.
module hs_stream_bridge(
    input clk, input rst, input ss, input mosi, output miso, input sck,
    output done, input [7:0] din, output [7:0] dout
);
    reg [7:0] in_ring, out_ring, dout_q;
    reg [2:0] bit_count;
    reg       sck_q, miso_q, done_q, parity_q;
    wire      rising = sck & ~sck_q;
    wire [7:0] next_in = {in_ring[6:0], mosi};
    wire [7:0] next_out = {out_ring[6:0], parity_q};

    assign miso = miso_q;
    assign done = done_q;
    assign dout = dout_q;

    always @(posedge clk) begin
        if (rst) begin
            in_ring   <= 8'd0;
            out_ring  <= 8'd0;
            dout_q    <= 8'd0;
            bit_count <= 3'd0;
            sck_q     <= 1'b0;
            miso_q    <= 1'b1;
            done_q    <= 1'b0;
            parity_q  <= 1'b0;
        end else begin
            sck_q  <= sck;
            done_q <= 1'b0;
            if (ss) begin
                bit_count <= 3'd0;
                out_ring  <= din;
                in_ring   <= 8'd0;
                parity_q  <= ^din;
                miso_q    <= din[7];
            end else if (rising) begin
                in_ring   <= next_in;
                out_ring  <= next_out;
                miso_q    <= out_ring[6];
                parity_q  <= parity_q ^ mosi;
                if (bit_count == 3'd7) begin
                    dout_q    <= next_in ^ {7'd0, parity_q};
                    done_q    <= 1'b1;
                    bit_count <= 3'd0;
                end else begin
                    bit_count <= bit_count + 1'b1;
                end
            end
        end
    end
endmodule

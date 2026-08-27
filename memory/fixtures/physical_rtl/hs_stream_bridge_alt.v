// Independent SPI-shaped bridge lineage with a dual-register receive path.
module hs_stream_bridge_alt(
    input clk, input rst, input ss, input mosi, output miso, input sck,
    output done, input [7:0] din, output [7:0] dout
);
    reg [7:0] shift_in, shift_out, dout_q;
    reg [2:0] count;
    reg sck_d, miso_q, done_q;
    reg parity;
    wire edge_seen = sck & ~sck_d;
    wire [7:0] sampled = {shift_in[6:0], mosi};
    wire [7:0] transmitted = {shift_out[6:0], parity};

    assign miso = miso_q;
    assign done = done_q;
    assign dout = dout_q;

    always @(posedge clk) begin
        if (rst) begin
            shift_in <= 0; shift_out <= 0; dout_q <= 0; count <= 0;
            sck_d <= 0; miso_q <= 1'b1; done_q <= 1'b0; parity <= 1'b0;
        end else begin
            sck_d <= sck;
            done_q <= 1'b0;
            if (ss) begin
                count <= 0;
                shift_in <= 0;
                shift_out <= din;
                parity <= ^din;
                miso_q <= din[7];
            end else if (edge_seen) begin
                shift_in <= sampled;
                shift_out <= transmitted;
                miso_q <= shift_out[6];
                parity <= parity ^ mosi;
                if (count == 3'd7) begin
                    dout_q <= sampled ^ {7'd0, parity};
                    done_q <= 1'b1;
                    count <= 0;
                end else begin
                    count <= count + 1'b1;
                end
            end
        end
    end
endmodule

from fpgatwin.constraints.ucf import parse_ucf_text

SAMPLE = """
# comment line
NET "clk"        LOC = "V10" | IOSTANDARD = LVCMOS25;
TIMESPEC "TS_clk" = PERIOD "clk" 37.037 ns HIGH 50%;
NET "led<0>"     LOC = "E13";
NET led<1>       LOC=C14 | IOSTANDARD=LVCMOS25 ;   # unquoted, trailing comment
NET "sw[2]"      LOC = "F12" | PULLDOWN;
NET "btn"        IOSTANDARD = LVCMOS18;
NET "btn"        LOC = "N4";
NET "multi"
    LOC = "P4";
INST "foo" AREA_GROUP = "bar";
CONFIG VCCAUX = "3.3";
"""


def test_basic_nets():
    r = parse_ucf_text(SAMPLE)
    assert r.warnings == []
    assert r.nets["clk"].loc == "V10"
    assert r.nets["clk"].iostandard == "LVCMOS25"
    assert (r.nets["led<0>"].port, r.nets["led<0>"].index) == ("led", 0)
    assert r.nets["led<1>"].loc == "C14"
    assert (r.nets["sw[2]"].port, r.nets["sw[2]"].index, r.nets["sw[2]"].pull) == ("sw", 2, "down")


def test_attributes_merge_across_lines():
    r = parse_ucf_text(SAMPLE)
    assert r.nets["btn"].loc == "N4"
    assert r.nets["btn"].iostandard == "LVCMOS18"


def test_multiline_statement():
    assert parse_ucf_text(SAMPLE).nets["multi"].loc == "P4"


def test_by_port_bit():
    keys = parse_ucf_text(SAMPLE).by_port_bit()
    assert keys[("led", 1)].loc == "C14"
    assert keys[("clk", None)].loc == "V10"


def test_warnings_for_unsupported_and_conflicts():
    r = parse_ucf_text('FOO bar;\nNET "a" LOC = "A1";\nNET "b" LOC = "A1";\nNET "x*" LOC = "B2";')
    text = "\n".join(r.warnings)
    assert "<ucf>:1: unsupported statement" in text
    assert "pin A1 assigned to both a and b" in text
    assert "wildcard" in text


# ---- XDC and mixed files ----------------------------------------------------------

# A Vivado constraint file saved as .ucf, with the old UCF lines commented out (CRLF, no ';')
XDC_AS_UCF = (
    "set_property PACKAGE_PIN N15 [get_ports PB[0]]\r\n"
    "set_property IOSTANDARD LVCMOS18 [get_ports PB[0]]\r\n"
    "\r\n"
    "set_property PACKAGE_PIN T22 [get_ports g]\r\n"
    "#NET LD0           LOC = T22  | IOSTANDARD=LVCMOS33;  # \"LD0\"\r\n"
    "set_property PACKAGE_PIN Y9 [get_ports clk]   # trailing comment\r\n"
    "create_clock -period 10.000 -name clk [get_ports clk]\r\n"
    "set_property CFGBVS VCCO [current_design]\r\n"
)


def test_xdc_in_ucf_file():
    r = parse_ucf_text(XDC_AS_UCF)
    assert r.warnings == []
    assert r.nets["PB[0]"].loc == "N15"
    assert (r.nets["PB[0]"].port, r.nets["PB[0]"].index) == ("PB", 0)
    assert r.nets["PB[0]"].iostandard == "LVCMOS18"
    assert r.nets["g"].loc == "T22"
    assert r.nets["clk"].loc == "Y9"
    assert "LD0" not in r.nets  # commented out


def test_xdc_forms():
    r = parse_ucf_text(
        "set_property -dict { PACKAGE_PIN E13  IOSTANDARD LVCMOS25 } [get_ports { led[0] }];\n"
        'set_property PACKAGE_PIN "C14" [get_ports "led[1]"]\n'
        "set_property PULLUP true [get_ports {btn}]\n"
        "set_property PACKAGE_PIN N4 " + "\\" + "\n    [get_ports btn]\n"  # Tcl continuation
        "set_property PULLTYPE PULLDOWN [get_ports {sw[0] sw[1]}]\n"
    )
    assert r.warnings == []
    assert (r.nets["led[0]"].loc, r.nets["led[0]"].iostandard) == ("E13", "LVCMOS25")
    assert r.nets["led[1]"].loc == "C14"
    assert (r.nets["btn"].loc, r.nets["btn"].pull) == ("N4", "up")
    assert r.nets["sw[0]"].pull == r.nets["sw[1]"].pull == "down"


def test_xdc_unsupported_forms_warn():
    r = parse_ucf_text("set_property PACKAGE_PIN A1 [get_ports -filter {NAME =~ x}]\n"
                       "set_property PACKAGE_PIN B1 [get_ports led*]\n")
    text = "\n".join(r.warnings)
    assert "<ucf>:1: unsupported get_ports option" in text
    assert "<ucf>:2: wildcard" in text


def test_missing_semicolon_does_not_swallow_next_lines():
    r = parse_ucf_text('NET "a" LOC = "A1"\nNET "b" LOC = "B1";\nset_property PACKAGE_PIN C1 [get_ports c]\n')
    assert (r.nets["a"].loc, r.nets["b"].loc, r.nets["c"].loc) == ("A1", "B1", "C1")
    assert r.warnings == ["<ucf>:1: missing ';' at the end of this statement"]

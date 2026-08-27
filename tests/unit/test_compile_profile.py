from hy3_contestlens.judge import CPP_COMPILE_FLAGS


def test_cpp_compile_profile_statically_links_cpp_runtime():
    assert "-static-libstdc++" in CPP_COMPILE_FLAGS
    assert "-static-libgcc" in CPP_COMPILE_FLAGS

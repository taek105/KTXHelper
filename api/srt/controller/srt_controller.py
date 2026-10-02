from service.ktx import KTX, get_schedule


def run_macro_logic(
    login_id: str,
    login_psw: str,
    dpt_stn: str,
    arr_stn: str,
    dpt_dt: str,
    dpt_tm: str,
    target: list[int],
    want_reserve: bool,
) -> bool:

    ktx = KTX(
        dpt_stn,
        arr_stn,
        dpt_dt,
        dpt_tm,
        target,
        want_reserve,
    )
    return ktx.run(login_id, login_psw)
    
    
def run_get_schedule(login_id, login_psw, dpt_stn, arr_stn, date, tm):
    
    return get_schedule(login_id, login_psw, dpt_stn, arr_stn, date, tm)

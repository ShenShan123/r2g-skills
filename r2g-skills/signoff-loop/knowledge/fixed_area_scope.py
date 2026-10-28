"""Read-only reuse of validated fixed-area geometry across design-class labels.

Class labels describe RTL families and scale, not floorplan constraints. Reuse
still requires the conservative current geometry diagnosis, a promoted anchor,
and two independent repeated physical A/B wins. Exact target-class vetoes are
handled before this function by the ordinary lifecycle resolver.
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING

KEY = dict(symptom_id='11f02dbb19046bb6', design_class='crypto/medium',
           platform='sky130hd', strategy='fixed_area_route_relief')


def matches(strategy):
    scope = strategy.get('fixed_area_scope_evidence') or {}
    geometry = strategy.get('geometry_evidence') or {}
    if (strategy.get('id') != KEY['strategy'] or scope.get('platform') != KEY['platform']
            or scope.get('standard_cell_generated_config') is not True
            or strategy.get('sdc_edits') or strategy.get('env_edits')
            or strategy.get('rerun_from') != 'floorplan' or strategy.get('recheck') != 'route'
            or not geometry.get('log_sha256') or not (geometry.get('def_sha256') or geometry.get('odb_sha256'))
            or type(geometry.get('standard_cell_count')) is not int or geometry['standard_cell_count'] <= 0):
        return False
    try:
        die = [Decimal(v) for v in scope['original_die'].split()]
        core = [Decimal(v) for v in scope['original_core'].split()]
        if len(die) != 4 or len(core) != 4 or not all(v.is_finite() for v in die+core):return False
        x,y,X,Y = die; a,b,A,B = core
        if not (x<a<A<X and y<b<B<Y):return False
        nx=x+((X-x)*Decimal('1.25')).to_integral_value(rounding=ROUND_CEILING)
        ny=y+((Y-y)*Decimal('1.25')).to_integral_value(rounding=ROUND_CEILING)
        edits=strategy.get('config_edits') or {}
        return (set(edits)=={'DIE_AREA','CORE_AREA'}
                and [Decimal(v) for v in edits['DIE_AREA'].split()]==[x,y,nx,ny]
                and [Decimal(v) for v in edits['CORE_AREA'].split()]==[a,b,nx-(X-A),ny-(Y-B)])
    except (KeyError,TypeError,ValueError,InvalidOperation):
        return False


def validated(conn, lifecycle, strategy):
    from physical_ab_scope import has_durable_repeated_wins
    return matches(strategy) and has_durable_repeated_wins(conn,lifecycle,KEY)

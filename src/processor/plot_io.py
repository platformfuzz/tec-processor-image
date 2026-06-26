"""Plot generation for calibrated TEC rows.

Ported from PyTECGg Batch Calibrator (tools/docker/calibrator.py) to produce
in-memory bytes suitable for S3 upload. Requires matplotlib and plotly.
"""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from typing import Any

CONSTELLATION_COLORS = {
    "G": "#4682B4",
    "E": "#009E73",
    "R": "#D55E00",
    "C": "#CC79A7",
}
CONSTELLATION_NAMES = {"G": "GPS", "E": "Galileo", "R": "GLONASS", "C": "BeiDou"}


def _rows_to_plot_df(rows: list[dict[str, Any]]) -> Any:
    """Convert rows to a Polars DataFrame with a parsed Datetime epoch column."""
    try:
        import polars as pl
    except Exception as exc:
        raise RuntimeError("polars is required for plot generation") from exc

    if not rows:
        raise ValueError("Cannot generate plot: no rows provided")

    df = pl.from_dicts(rows).select(["epoch", "sv", "stec", "vtec", "veq", "ele"])
    df = df.with_columns(
        pl.col("epoch")
        .str.replace(r"Z$", "+00:00")
        .str.to_datetime(format="%Y-%m-%dT%H:%M:%S%z", time_unit="us")
        .alias("epoch")
    )
    return df


def _date_str(year: int, doy: int) -> str:
    return (date(year, 1, 1) + timedelta(days=doy - 1)).strftime("%Y-%m-%d")


def _is_multi_day(df: Any) -> bool:
    try:
        duration = (df["epoch"].max() - df["epoch"].min()).total_seconds()
        return duration > 86_400
    except Exception:
        return False


def rows_to_static_plot_bytes(
    rows: list[dict[str, Any]],
    station: str,
    year: int,
    doy: int,
    dpi: int = 150,
) -> bytes:
    """Render a static PNG TEC plot and return its bytes.

    Args:
        rows: Calibrated TEC rows (output contract list[dict]).
        station: Station name shown in plot title.
        year: Observation year for date label.
        doy: Observation day-of-year for date label.
        dpi: PNG resolution. Default 150 (lower than Batch Calibrator's 300
             to keep Lambda/container artifact sizes reasonable).

    Returns:
        PNG image bytes.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise RuntimeError("matplotlib is required for static plot generation") from exc

    try:
        import polars as pl
    except Exception as exc:
        raise RuntimeError("polars is required for static plot generation") from exc

    df = _rows_to_plot_df(rows)
    date_label = _date_str(year, doy)

    bg_color = "#FFFFFF"
    veq_color = "#212121"
    grid_color = "#dee2e6"

    fig, ax = plt.subplots(figsize=(11, 5))
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(grid_color)
    ax.spines["bottom"].set_color(grid_color)
    ax.yaxis.grid(True, linestyle="--", alpha=0.8, color=grid_color)
    ax.set_axisbelow(True)
    ax.tick_params(colors=veq_color, which="both", labelsize=11)
    ax.set_title(
        f"TEC over {station.upper()} station",
        loc="left",
        fontsize=18,
        fontweight="bold",
        pad=25,
        color=veq_color,
    )
    ax.text(
        0,
        1.03,
        f"Calibrated sTEC and VEq \u2013 {date_label}",
        transform=ax.transAxes,
        fontsize=14,
        fontweight="normal",
        color=veq_color,
        alpha=0.8,
    )

    df_with_const = df.with_columns(pl.col("sv").str.slice(0, 1).alias("constellation"))
    for const in ["G", "E", "R", "C"]:
        df_const = df_with_const.filter(pl.col("constellation") == const)
        if len(df_const) > 0:
            ax.scatter(
                df_const["epoch"].to_list(),
                df_const["stec"].to_list(),
                color=CONSTELLATION_COLORS.get(const),
                alpha=0.5,
                s=3.25,
                edgecolor="none",
                label=f"sTEC ({CONSTELLATION_NAMES.get(const, const)})",
            )

    df_veq = df.select(["epoch", "veq"]).unique().sort("epoch")
    ax.plot(
        df_veq["epoch"].to_list(),
        df_veq["veq"].to_list(),
        color=veq_color,
        linewidth=3.5,
        zorder=10,
        label="VEq",
    )

    ax.set_ylabel("TECu", fontsize=13, color=veq_color)
    ax.set_xlabel("Epoch (UTC)", fontsize=13, color=veq_color, labelpad=10)
    ax.legend(
        loc="upper right",
        frameon=True,
        fontsize=10,
        markerscale=2.5,
        facecolor="w",
        framealpha=1,
        edgecolor="none",
        borderpad=0.7,
    )

    epoch_list = df["epoch"].to_list()
    ax.set_xlim(min(epoch_list), max(epoch_list))
    if _is_multi_day(df):
        ax.xaxis.set_major_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    else:
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    buf = BytesIO()
    plt.savefig(buf, dpi=dpi, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def rows_to_interactive_plot_bytes(
    rows: list[dict[str, Any]],
    station: str,
    year: int,
    doy: int,
) -> bytes:
    """Render an interactive HTML TEC plot and return its bytes.

    Args:
        rows: Calibrated TEC rows (output contract list[dict]).
        station: Station name shown in plot title.
        year: Observation year for date label.
        doy: Observation day-of-year for date label.

    Returns:
        UTF-8 encoded HTML bytes (self-contained via CDN plotly.js).
    """
    try:
        import plotly.graph_objects as go
    except Exception as exc:
        raise RuntimeError("plotly is required for interactive plot generation") from exc

    try:
        import polars as pl
    except Exception as exc:
        raise RuntimeError("polars is required for interactive plot generation") from exc

    df = _rows_to_plot_df(rows)
    date_label = _date_str(year, doy)

    bg_color = "#FFFFFF"
    veq_color = "#212121"
    grid_color = "#dee2e6"

    fig = go.Figure()

    df_with_const = df.with_columns(pl.col("sv").str.slice(0, 1).alias("constellation"))
    for const in ["G", "E", "R", "C"]:
        df_const = df_with_const.filter(pl.col("constellation") == const)
        if len(df_const) > 0:
            fig.add_trace(
                go.Scatter(
                    x=df_const["epoch"].to_list(),
                    y=df_const["stec"].to_list(),
                    mode="markers",
                    marker=dict(
                        color=CONSTELLATION_COLORS.get(const, "#808080"),
                        size=4,
                        opacity=0.45,
                    ),
                    name=f"sTEC ({CONSTELLATION_NAMES.get(const, const)})",
                    text=df_const["sv"].to_list(),
                    hovertemplate=(
                        "<b>SV:</b> %{text}<br>"
                        "<b>Time:</b> %{x|%Y-%m-%d %H:%M:%S}<br>"
                        "<b>sTEC:</b> %{y:.2f} TECu<extra></extra>"
                    ),
                )
            )

    df_veq = df.select(["epoch", "veq"]).unique().sort("epoch")
    fig.add_trace(
        go.Scatter(
            x=df_veq["epoch"].to_list(),
            y=df_veq["veq"].to_list(),
            mode="lines",
            line=dict(color=veq_color, width=3.5),
            name="VEq",
            hovertemplate=(
                "<b>Time:</b> %{x|%Y-%m-%d %H:%M:%S}<br>"
                "<b>VEq:</b> %{y:.2f} TECu<extra></extra>"
            ),
        )
    )

    x_tickformat = "%d %b\n%Y" if _is_multi_day(df) else "%H:%M"
    fig.update_layout(
        title=dict(
            text=(
                f"<b>TEC over {station.upper()} station</b>"
                f"<br><sup>Calibrated sTEC and VEq \u2013 {date_label}</sup>"
            ),
            font=dict(size=20, color=veq_color, family="Arial, sans-serif"),
            x=0.02,
            y=0.95,
        ),
        plot_bgcolor=bg_color,
        paper_bgcolor=bg_color,
        font=dict(family="Arial, sans-serif", color=veq_color),
        xaxis=dict(
            title="Epoch (UTC)",
            showgrid=True,
            gridcolor=grid_color,
            gridwidth=1,
            griddash="dash",
            linecolor=grid_color,
            tickformat=x_tickformat,
        ),
        yaxis=dict(
            title="TECu",
            showgrid=True,
            gridcolor=grid_color,
            gridwidth=1,
            griddash="dash",
            linecolor=grid_color,
        ),
        legend=dict(
            bgcolor="white",
            bordercolor="rgba(0,0,0,0)",
            x=0.99,
            y=0.99,
            xanchor="right",
            yanchor="top",
        ),
        margin=dict(l=60, r=30, t=100, b=60),
    )

    return fig.to_html(include_plotlyjs="cdn").encode("utf-8")

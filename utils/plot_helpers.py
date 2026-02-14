import plotly.graph_objects as go

def apply_legend_bottom(fig: go.Figure) -> go.Figure:
    """
    Korrektur der Legendenposition für alle Charts.
    Setzt die Legende horizontal unter das Chart.
    
    Args:
        fig: Das Plotly-Figure-Objekt
        
    Returns:
        Das aktualisierte Figure-Objekt
    """
    fig.update_layout(
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.20,
            xanchor="center",
            x=0.5
        ),
        margin=dict(b=90)
    )
    return fig

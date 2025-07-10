import json
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

def load_performance_data_from_json(json_filename):
    """
    Loads performance data from a local JSON file into a pandas DataFrame.
    """
    try:
        with open(json_filename, 'r') as f:
            # The JSON file should contain an array of objects
            data = json.load(f)
            # Make sure all relevant columns are treated as numeric for sorting/plotting
            numeric_cols = [
                'avg_total_tps', 'peak_total_tps', 
                'avg_tps_per_gpu', 'peak_tps_per_gpu',
                'num_samples', 'gpu_count'
            ]
            df = pd.DataFrame(data)
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col])
            print(f"Successfully loaded and parsed {json_filename}.")
            return df
    except FileNotFoundError:
        print(f"Error: The file '{json_filename}' was not found.")
        print("Please run the SQL query and save the output to this file.")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error: Could not parse the JSON file. Please ensure it's a valid array of objects. Details: {e}")
        return None

def create_performance_histogram(df, metric_col, title, xlabel, output_filename, fmt='%.1f'):
    """
    Generates and saves a bar chart from the performance data for a given metric.
    """
    if df is None or df.empty:
        print("No data to visualize.")
        return
    
    if metric_col not in df.columns:
        print(f"Metric column '{metric_col}' not found in DataFrame. Skipping plot.")
        return

    # Create a new column for y-axis labels that includes GPU and sample counts
    if 'gpu_count' in df.columns and 'num_samples' in df.columns:
        df['display_label'] = df.apply(
            lambda row: f"{row['model']} (GPUs: {int(row['gpu_count'])}, Samples: {int(row['num_samples'])})",
            axis=1
        )
        y_axis_label = "Model (GPUs, Samples)"
        y_col = 'display_label'
    else:
        print("Warning: 'gpu_count' or 'num_samples' not found. Using model name as label.")
        y_axis_label = "Model"
        y_col = 'model'


    # Sort the data by the metric for a more readable chart
    df = df.sort_values(by=metric_col, ascending=False)

    print(f"Generating plot for {metric_col}... saving to {output_filename}")

    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(14, 12)) # Increased size for better label spacing

    sns.barplot(
        x=metric_col,
        y=y_col,
        data=df,
        ax=ax,
        palette="viridis_r"
    )

    ax.set_title(title, fontsize=18, weight='bold')
    ax.set_xlabel(xlabel, fontsize=14)
    ax.set_ylabel(y_axis_label, fontsize=14)
    ax.tick_params(axis='y', labelsize=12) # Adjust y-axis label size

    for container in ax.containers:
        ax.bar_label(container, fmt=fmt, padding=3, fontsize=11)

    plt.tight_layout()
    plt.savefig(output_filename, dpi=300) # Save with higher resolution
    print(f"Plot saved successfully as '{output_filename}'")
    plt.close()


def main():
    """Main function to execute the script."""
    json_file = 'performance_data.json'
    performance_df = load_performance_data_from_json(json_file)
    if performance_df is not None:
        metrics_to_plot = {
            'peak_tps_per_gpu': {
                'title': 'Peak Tokens per Second per GPU',
                'xlabel': 'Peak Throughput (Tokens/Second/GPU)',
                'filename': 'peak_tps_per_gpu_histogram.png',
                'fmt': '%.1f'
            },
            'avg_tps_per_gpu': {
                'title': 'Average Tokens per Second per GPU',
                'xlabel': 'Average Throughput (Tokens/Second/GPU)',
                'filename': 'avg_tps_per_gpu_histogram.png',
                'fmt': '%.1f'
            },
            'peak_total_tps': {
                'title': 'Peak Total Tokens per Second (Across all GPUs)',
                'xlabel': 'Peak Total Throughput (Tokens/Second)',
                'filename': 'peak_total_tps_histogram.png',
                'fmt': '%.1f'
            },
            'avg_total_tps': {
                'title': 'Average Total Tokens per Second (Across all GPUs)',
                'xlabel': 'Average Total Throughput (Tokens/Second)',
                'filename': 'avg_total_tps_histogram.png',
                'fmt': '%.1f'
            }
        }

        for metric, details in metrics_to_plot.items():
            create_performance_histogram(
                performance_df.copy(), # Use a copy to avoid side effects
                metric_col=metric,
                title=details['title'],
                xlabel=details['xlabel'],
                output_filename=details['filename'],
                fmt=details['fmt']
            )


if __name__ == "__main__":
    main() 

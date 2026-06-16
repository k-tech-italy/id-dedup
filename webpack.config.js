const path = require('path')

const static_dir = path.resolve(__dirname, 'src/id_dedup/static/js');

const NODE_WATCH = process.env.NODE_WATCH;

module.exports = {
  mode: 'development',
  watch: NODE_WATCH == '1',
  watchOptions: {
    aggregateTimeout: 800,
    poll: 2000,
  },

  entry: './src/id_dedup/assets/index.js',
  output: {
    path: static_dir,
    filename: 'bundle.js',
  },
  devServer: {
    static: {
      directory: static_dir,
    },
    port: 3000,
    open: true,
    hot: true,
    compress: true,
    historyApiFallback: true,
  },
  module: {
    rules: [
      {
        test: /\.css$/i,
        include: path.resolve(__dirname, 'src'),
        use: ['style-loader', 'css-loader', 'postcss-loader'],
      },
    ],
  },
}